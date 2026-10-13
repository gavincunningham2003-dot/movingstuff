"""Microsoft US Building Footprints (California) → ``buildings`` + ``address_buildings``.

Matching rule (documented uncertainty):

* ``contains``  — the address point falls inside a footprint → uncertainty *low*.
* ``nearest``   — nearest footprint within 30 m → *medium* (≤10 m) or *high* (10–30 m),
                  because address points are frequently parcel centroids or street-side
                  interpolations rather than rooftop points.
* ``none``      — no footprint within 30 m → *unmatched*.

``buildings_within_100m`` is recorded as a density proxy.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from catrix.config import GEOGRAPHIC_CRS, PROJECTED_CRS
from catrix.db.models import Address, AddressBuilding, Building, Region
from catrix.geo.spatial import count_within_radius, nearest_distance_m
from catrix.ingest.base import IngestResult
from catrix.ingest.http import SourceUnavailableError, download_file
from catrix.ingest.registry import load_registry, mark_failed, mark_ingested
from catrix.ingest.store import write_parquet
from catrix.logging import get_logger

log = get_logger(__name__)

SOURCE_ID = "ms_building_footprints_ca"
MAX_MATCH_M = 30.0


def match_addresses_to_buildings(
    addresses: gpd.GeoDataFrame, buildings: gpd.GeoDataFrame
) -> pd.DataFrame:
    """Pure function used by the connector and by tests. ``addresses`` must carry an
    ``address_id`` column, ``buildings`` a ``building_id`` column; both in EPSG:4326."""
    a = addresses.to_crs(PROJECTED_CRS)
    b = buildings.to_crs(PROJECTED_CRS)
    out = pd.DataFrame(
        {
            "address_id": a["address_id"].values,
            "building_id": pd.array([None] * len(a), dtype="Int64"),
            "match_method": "none",
            "match_distance_m": np.nan,
            "buildings_within_100m": 0,
            "uncertainty": "unmatched",
        }
    )
    if len(b) == 0:
        return out
    within = gpd.sjoin(
        a[["address_id", "geometry"]],
        b[["building_id", "geometry"]],
        how="left",
        predicate="within",
    )
    within = within.groupby("address_id").first()
    contained = within["building_id"].notna()
    out = out.set_index("address_id")
    out.loc[contained[contained].index, "building_id"] = within.loc[
        contained, "building_id"
    ].astype("Int64")
    out.loc[contained[contained].index, "match_method"] = "contains"
    out.loc[contained[contained].index, "match_distance_m"] = 0.0
    out.loc[contained[contained].index, "uncertainty"] = "low"

    remaining_ids = out.index[out["match_method"] == "none"]
    if len(remaining_ids) > 0:
        rem = a[a["address_id"].isin(remaining_ids)]
        near = nearest_distance_m(rem, b, max_distance_m=MAX_MATCH_M)
        matched = near.index >= 0
        ids = rem["address_id"].values[matched]
        out.loc[ids, "building_id"] = pd.array(
            b["building_id"].values[near.index[matched]], dtype="Int64"
        )
        out.loc[ids, "match_method"] = "nearest"
        out.loc[ids, "match_distance_m"] = near.distance_m[matched]
        out.loc[ids, "uncertainty"] = np.where(near.distance_m[matched] <= 10.0, "medium", "high")
    out["buildings_within_100m"] = count_within_radius(a, b, 100.0)
    return out.reset_index()


class BuildingsConnector:
    source_id = SOURCE_ID

    def run(self, session: Session, *, force: bool = False, **_: Any) -> IngestResult:
        spec = load_registry().get(SOURCE_ID)
        assert spec.download_url
        addresses = gpd.read_postgis(
            select(Address.id.label("address_id"), Address.county_fips, Address.geom),
            session.connection(),
            geom_col="geom",
            crs=GEOGRAPHIC_CRS,
        ).rename_geometry("geometry")
        if len(addresses) == 0:
            msg = "no addresses ingested; run openaddresses first"
            mark_failed(session, SOURCE_ID, msg)
            return IngestResult(SOURCE_ID, "failed", message=msg)
        fips = sorted(addresses["county_fips"].dropna().unique())
        regions = gpd.read_postgis(
            select(Region.fips, Region.geom).where(Region.fips.in_(fips)),
            session.connection(),
            geom_col="geom",
            crs=GEOGRAPHIC_CRS,
        ).rename_geometry("geometry")
        try:
            dl = download_file(
                SOURCE_ID, spec.download_url, force=force, expected_min_bytes=100_000_000
            )
        except SourceUnavailableError as exc:
            mark_failed(session, SOURCE_ID, str(exc), unavailable=True)
            return IngestResult(SOURCE_ID, "unavailable", message=str(exc))

        # One sequential pass per county bbox is wasteful on a ~GB GeoJSON; read once with the
        # union bbox and clip to county polygons afterwards.
        minx, miny, maxx, maxy = regions.total_bounds
        log.info("buildings_reading", bbox=(minx, miny, maxx, maxy))
        try:
            gdf = pyogrio.read_dataframe(f"zip://{dl.path}", bbox=(minx, miny, maxx, maxy))
        except Exception as exc:
            mark_failed(session, SOURCE_ID, f"read failed: {exc!r}")
            return IngestResult(SOURCE_ID, "failed", message=str(exc))
        gdf = gdf.set_crs(GEOGRAPHIC_CRS, allow_override=True)
        gdf = gdf[gdf.geometry.notna() & gdf.geometry.is_valid]
        # Keep only footprints near sampled addresses (within 150 m) to bound storage.
        near = gpd.sjoin(
            gdf.to_crs(PROJECTED_CRS),
            addresses.to_crs(PROJECTED_CRS).assign(geometry=lambda d: d.geometry.buffer(150))[
                ["geometry"]
            ],
            how="inner",
            predicate="intersects",
        )
        gdf = gdf.loc[near.index.unique()]
        gdf = gdf[gdf.geometry.geom_type == "Polygon"]
        gdf["footprint_area_m2"] = gdf.to_crs(PROJECTED_CRS).geometry.area
        county_join = gpd.sjoin(
            gdf[["geometry"]].assign(c=gdf.geometry.centroid).set_geometry("c"),
            regions,
            how="left",
            predicate="within",
        )
        gdf["county_fips"] = county_join.groupby(level=0)["fips"].first()

        session.execute(delete(AddressBuilding))
        session.execute(delete(Building).where(Building.source_id == SOURCE_ID))
        rows = [
            {
                "source_id": SOURCE_ID,
                "footprint_area_m2": float(r.footprint_area_m2),
                "release": str(getattr(r, "release", "")) or None,
                "capture_dates_range": str(getattr(r, "capture_dates_range", "")) or None,
                "county_fips": r.county_fips if isinstance(r.county_fips, str) else None,
                "geom": f"SRID=4326;{r.geometry.wkt}",
            }
            for r in gdf.itertuples()
        ]
        session.bulk_insert_mappings(Building, rows)
        session.flush()
        bld = gpd.read_postgis(
            select(Building.id.label("building_id"), Building.geom).where(
                Building.source_id == SOURCE_ID
            ),
            session.connection(),
            geom_col="geom",
            crs=GEOGRAPHIC_CRS,
        ).rename_geometry("geometry")
        matches = match_addresses_to_buildings(addresses, bld)
        session.bulk_insert_mappings(
            AddressBuilding,
            [
                {
                    "address_id": int(m.address_id),
                    "building_id": int(m.building_id) if pd.notna(m.building_id) else None,
                    "match_method": m.match_method,
                    "match_distance_m": None
                    if pd.isna(m.match_distance_m)
                    else float(m.match_distance_m),
                    "buildings_within_100m": int(m.buildings_within_100m),
                    "uncertainty": m.uncertainty,
                }
                for m in matches.itertuples()
            ],
        )
        session.flush()
        write_parquet(matches, "address_building_matches")
        quality = {
            "n_footprints_kept": len(rows),
            "match_methods": matches["match_method"].value_counts().to_dict(),
            "uncertainty": matches["uncertainty"].value_counts().to_dict(),
            "max_match_distance_m": MAX_MATCH_M,
        }
        mark_ingested(
            session,
            SOURCE_ID,
            record_count=len(rows),
            version="v2",
            checksum=dl.sha256,
            extra=quality,
        )
        return IngestResult(SOURCE_ID, "ingested", len(rows), "v2", quality=quality)

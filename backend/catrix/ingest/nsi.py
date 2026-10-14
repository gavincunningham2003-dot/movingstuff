"""USACE National Structure Inventory → ``structures`` + ``address_structures``.

One GeoJSON request per county FIPS (verified: ``?fips=06003``). Responses for the largest
counties are multi-gigabyte, so counties whose structure count is expected to exceed
``CATRIX_NSI_MAX_STRUCTURES_PER_COUNTY`` are skipped unless ``include_large`` is set; the
skip is recorded in the source's quality metadata and such addresses stay unmatched.

Matching: nearest structure point within 30 m (NSI points are footprint centroids);
uncertainty low (≤ 5 m), medium (≤ 15 m), high (≤ 30 m), otherwise unmatched.
"""

from __future__ import annotations

import json
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from catrix.config import GEOGRAPHIC_CRS, get_settings
from catrix.db.models import Address, AddressStructure, Structure
from catrix.geo.spatial import nearest_distance_m
from catrix.ingest.base import IngestResult
from catrix.ingest.http import SourceUnavailableError, download_file
from catrix.ingest.registry import load_registry, mark_failed, mark_ingested
from catrix.ingest.store import rows as iter_rows
from catrix.ingest.store import write_parquet
from catrix.logging import get_logger

log = get_logger(__name__)

SOURCE_ID = "usace_nsi"
MAX_MATCH_M = 30.0
REQUIRED = [
    "fd_id",
    "bldgtype",
    "occtype",
    "num_story",
    "found_type",
    "found_ht",
    "ground_elv",
    "med_yr_blt",
    "val_struct",
]
# Rough NSI structure counts; counties above the cap are skipped by default.
LARGE_COUNTIES = {
    "06037": 3_000_000,
    "06073": 1_000_000,
    "06059": 900_000,
    "06065": 850_000,
    "06071": 750_000,
    "06085": 550_000,
    "06001": 500_000,
    "06067": 500_000,
}


def _uncertainty(d: float) -> str:
    if d <= 5:
        return "low"
    if d <= 15:
        return "medium"
    return "high"


def parse_nsi_geojson(path) -> gpd.GeoDataFrame:
    """Read an NSI FeatureCollection; validates the fields the vulnerability module needs."""
    gdf = pyogrio.read_dataframe(str(path))
    missing = [c for c in REQUIRED if c not in gdf.columns]
    if missing:
        raise SourceUnavailableError(SOURCE_ID, str(path), f"NSI schema drift: missing {missing}")
    gdf = gdf.set_crs(GEOGRAPHIC_CRS, allow_override=True)
    return gdf


def structures_from_gdf(gdf: gpd.GeoDataFrame, county_fips: str) -> list[dict[str, Any]]:
    def _f(v: Any) -> float | None:
        return (
            None
            if v is None or (isinstance(v, float) and np.isnan(v)) or v in (-9999, -1)
            else float(v)
        )

    rows = []
    for r in gdf.itertuples(index=False):
        d = r._asdict()
        g = d.pop("geometry")
        if g is None or g.is_empty:
            continue
        rows.append(
            {
                "id": int(d["fd_id"]),
                "source_id": SOURCE_ID,
                "bid": d.get("bid"),
                "bldgtype": d.get("bldgtype"),
                "occtype": d.get("occtype"),
                "damage_category": d.get("st_damcat"),
                "num_story": int(d["num_story"]) if _f(d.get("num_story")) is not None else None,
                "sqft": _f(d.get("sqft")),
                "found_type": d.get("found_type"),
                "found_ht_ft": _f(d.get("found_ht")),
                "ground_elev_ft": _f(d.get("ground_elv")),
                "year_built": int(d["med_yr_blt"]) if _f(d.get("med_yr_blt")) else None,
                "val_struct_usd": _f(d.get("val_struct")),
                "val_cont_usd": _f(d.get("val_cont")),
                "firmzone": d.get("firmzone"),
                "static_bfe_ft": _f(d.get("static_bfe")),
                "county_fips": county_fips,
                "geom": f"SRID=4326;POINT({g.x} {g.y})",
            }
        )
    return rows


def match_addresses_to_structures(
    addresses: gpd.GeoDataFrame, structures: gpd.GeoDataFrame
) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "address_id": addresses["address_id"].values,
            "structure_id": pd.array([None] * len(addresses), dtype="Int64"),
            "match_distance_m": np.nan,
            "uncertainty": "unmatched",
        }
    )
    if len(structures) == 0 or len(addresses) == 0:
        return out
    near = nearest_distance_m(addresses, structures, max_distance_m=MAX_MATCH_M)
    ok = near.index >= 0
    out.loc[ok, "structure_id"] = pd.array(
        structures["structure_id"].values[near.index[ok]], dtype="Int64"
    )
    out.loc[ok, "match_distance_m"] = near.distance_m[ok]
    out.loc[ok, "uncertainty"] = [_uncertainty(d) for d in near.distance_m[ok]]
    return out


class NSIConnector:
    source_id = SOURCE_ID

    def run(
        self, session: Session, *, force: bool = False, include_large: bool = False, **_: Any
    ) -> IngestResult:
        settings = get_settings()
        spec = load_registry().get(SOURCE_ID)
        assert spec.api_url
        addresses = gpd.read_postgis(
            select(Address.id.label("address_id"), Address.county_fips, Address.geom),
            session.connection(),
            geom_col="geom",
            crs=GEOGRAPHIC_CRS,
        ).rename_geometry("geometry")
        if len(addresses) == 0:
            mark_failed(session, SOURCE_ID, "no addresses ingested")
            return IngestResult(SOURCE_ID, "failed", message="no addresses ingested")
        counties = sorted(addresses["county_fips"].dropna().unique())
        skipped: list[str] = []
        failed: list[str] = []
        session.execute(delete(AddressStructure))
        session.execute(delete(Structure).where(Structure.source_id == SOURCE_ID))
        total = 0
        match_parts: list[pd.DataFrame] = []
        for fips in counties:
            if (
                not include_large
                and LARGE_COUNTIES.get(fips, 0) > settings.nsi_max_structures_per_county
            ):
                skipped.append(fips)
                log.warning("nsi_county_skipped_large", fips=fips)
                continue
            url = spec.api_url.format(fips=fips)
            try:
                dl = download_file(
                    SOURCE_ID,
                    url,
                    dest=spec.raw_dir / f"nsi_{fips}.geojson",
                    force=force,
                    expected_min_bytes=100,
                )
                gdf = parse_nsi_geojson(dl.path)
            except SourceUnavailableError as exc:
                failed.append(fips)
                log.error("nsi_county_failed", fips=fips, error=str(exc))
                continue
            # Keep only structures near sampled addresses (within 100 m) to bound storage.
            addr_c = addresses[addresses["county_fips"] == fips]
            near = gpd.sjoin(
                gdf.to_crs("EPSG:3310"),
                addr_c.to_crs("EPSG:3310").assign(geometry=lambda d: d.geometry.buffer(100))[
                    ["geometry"]
                ],
                how="inner",
                predicate="intersects",
            )
            gdf = gdf.loc[near.index.unique()]
            rows = structures_from_gdf(gdf, fips)
            if rows:
                session.bulk_insert_mappings(Structure, rows)
                session.flush()
            total += len(rows)
            st = gpd.GeoDataFrame(
                {"structure_id": [r["id"] for r in rows]},
                geometry=gpd.points_from_xy(
                    [float(r["geom"].split("(")[1].split()[0]) for r in rows],
                    [float(r["geom"].split()[-1].rstrip(")")) for r in rows],
                ),
                crs=GEOGRAPHIC_CRS,
            )
            match_parts.append(match_addresses_to_structures(addr_c, st))
            log.info("nsi_county_ingested", fips=fips, kept=len(rows))
        matches = (
            pd.concat(match_parts, ignore_index=True)
            if match_parts
            else pd.DataFrame(
                columns=["address_id", "structure_id", "match_distance_m", "uncertainty"]
            )
        )
        unmatched_ids = set(addresses["address_id"]) - set(matches["address_id"])
        rows_m = [
            {
                "address_id": int(m.address_id),
                "structure_id": int(m.structure_id) if pd.notna(m.structure_id) else None,
                "match_distance_m": None
                if pd.isna(m.match_distance_m)
                else float(m.match_distance_m),
                "uncertainty": m.uncertainty,
            }
            for m in iter_rows(matches)
        ] + [
            {
                "address_id": int(a),
                "structure_id": None,
                "match_distance_m": None,
                "uncertainty": "unmatched",
            }
            for a in unmatched_ids
        ]
        session.bulk_insert_mappings(AddressStructure, rows_m)
        session.flush()
        write_parquet(pd.DataFrame(rows_m), "address_structure_matches")
        quality = {
            "counties": counties,
            "skipped_large_counties": skipped,
            "failed_counties": failed,
            "n_structures_kept": total,
            "uncertainty": pd.Series([r["uncertainty"] for r in rows_m]).value_counts().to_dict(),
            "max_match_distance_m": MAX_MATCH_M,
        }
        if total == 0:
            mark_failed(
                session,
                SOURCE_ID,
                f"no structures ingested (failed={failed}, skipped={skipped})",
                unavailable=bool(failed and not skipped),
            )
            return IngestResult(
                SOURCE_ID, "unavailable" if failed else "failed", 0, message=json.dumps(quality)
            )
        mark_ingested(session, SOURCE_ID, record_count=total, version="NSI-2022", extra=quality)
        return IngestResult(SOURCE_ID, "ingested", total, "NSI-2022", quality=quality)

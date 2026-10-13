"""TIGER/Line counties → ``regions`` (California only)."""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import pyogrio
from sqlalchemy import delete
from sqlalchemy.orm import Session

from catrix.config import GEOGRAPHIC_CRS
from catrix.db.models import Region
from catrix.ingest.base import IngestResult
from catrix.ingest.http import SourceUnavailableError, download_file
from catrix.ingest.registry import load_registry, mark_failed, mark_ingested
from catrix.ingest.store import clean_polygons, write_parquet
from catrix.logging import get_logger

log = get_logger(__name__)

SOURCE_ID = "census_tiger_counties"
REQUIRED = ["STATEFP", "COUNTYFP", "GEOID", "NAME", "NAMELSAD", "ALAND"]


def load_california_counties(force: bool = False) -> gpd.GeoDataFrame:
    spec = load_registry().get(SOURCE_ID)
    assert spec.url
    dl = download_file(SOURCE_ID, spec.url, force=force, expected_min_bytes=10_000_000)
    # pyogrio reads shapefiles inside zips directly.
    gdf = pyogrio.read_dataframe(f"zip://{dl.path}", where="STATEFP = '06'")
    missing = [c for c in REQUIRED if c not in gdf.columns]
    if missing:
        raise SourceUnavailableError(SOURCE_ID, spec.url, f"schema drift, missing {missing}")
    gdf = gdf.to_crs(GEOGRAPHIC_CRS)
    gdf, dropped = clean_polygons(gdf)
    if len(gdf) != 58:
        log.warning("county_count_unexpected", n=len(gdf), expected=58)
    gdf.attrs["retrieved_at"] = dl.retrieved_at.isoformat()
    gdf.attrs["sha256"] = dl.sha256
    return gdf


class CountiesConnector:
    source_id = SOURCE_ID

    def run(self, session: Session, *, force: bool = False, **_: Any) -> IngestResult:
        try:
            gdf = load_california_counties(force=force)
        except SourceUnavailableError as exc:
            mark_failed(session, SOURCE_ID, str(exc), unavailable=True)
            return IngestResult(SOURCE_ID, "unavailable", message=str(exc))
        session.execute(delete(Region).where(Region.region_type == "county"))
        rows = [
            Region(
                region_type="county",
                fips=r.GEOID,
                name=r.NAME,
                name_lsad=r.NAMELSAD,
                land_area_m2=float(r.ALAND) if r.ALAND is not None else None,
                geom=f"SRID=4326;{r.geometry.wkt}",
            )
            for r in gdf.itertuples()
        ]
        session.add_all(rows)
        session.flush()
        write_parquet(gdf[["GEOID", "NAME", "NAMELSAD", "ALAND", "geometry"]], "regions_counties")
        mark_ingested(
            session,
            SOURCE_ID,
            record_count=len(rows),
            version="TIGER2023",
            checksum=gdf.attrs.get("sha256"),
        )
        return IngestResult(SOURCE_ID, "ingested", len(rows), "TIGER2023")

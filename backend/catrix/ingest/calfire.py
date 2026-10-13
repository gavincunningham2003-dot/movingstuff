"""CAL FIRE FRAP historical fire perimeters → ``wildfire_events``.

Verified service: ``California_Historic_Fire_Perimeters/FeatureServer/0`` (23,334 features
on 2026-10-13; EPSG:3857; maxRecordCount 2000; pagination supported).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import geopandas as gpd
import pandas as pd
from sqlalchemy import delete
from sqlalchemy.orm import Session

from catrix.db.models import WildfireEvent
from catrix.ingest.arcgis import fetch_layer
from catrix.ingest.base import IngestResult
from catrix.ingest.http import SourceUnavailableError
from catrix.ingest.registry import load_registry, mark_failed, mark_ingested
from catrix.ingest.store import clean_polygons, write_parquet
from catrix.logging import get_logger

log = get_logger(__name__)

SOURCE_ID = "calfire_fire_perimeters"
FIELDS = [
    "OBJECTID",
    "YEAR_",
    "STATE",
    "AGENCY",
    "UNIT_ID",
    "FIRE_NAME",
    "INC_NUM",
    "IRWINID",
    "ALARM_DATE",
    "CONT_DATE",
    "CAUSE",
    "C_METHOD",
    "OBJECTIVE",
    "GIS_ACRES",
    "COMPLEX_NAME",
]


def _epoch_ms_to_date(v: Any) -> Any:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return datetime.fromtimestamp(int(v) / 1000, tz=UTC).date()
    except (TypeError, ValueError, OSError):
        return None


class CalFirePerimetersConnector:
    source_id = SOURCE_ID

    def run(
        self,
        session: Session,
        *,
        force: bool = False,
        min_year: int = 1950,
        bbox: tuple[float, float, float, float] | None = None,
        **_: Any,
    ) -> IngestResult:
        spec = load_registry().get(SOURCE_ID)
        assert spec.api_url
        session.execute(delete(WildfireEvent).where(WildfireEvent.source_id == SOURCE_ID))
        total = 0
        dropped_total = 0
        years: list[int] = []
        parquet_parts: list[gpd.GeoDataFrame] = []

        def persist(page: gpd.GeoDataFrame) -> None:
            nonlocal total, dropped_total
            if len(page) == 0:
                return
            page, dropped = clean_polygons(page)
            dropped_total += dropped
            rows = []
            for r in page.itertuples():
                year = int(r.YEAR_) if pd.notna(r.YEAR_) else None
                if year is not None:
                    years.append(year)
                rows.append(
                    {
                        "source_id": SOURCE_ID,
                        "source_object_id": int(r.OBJECTID),
                        "year": year,
                        "fire_name": r.FIRE_NAME,
                        "agency": r.AGENCY,
                        "unit_id": r.UNIT_ID,
                        "inc_num": r.INC_NUM,
                        "irwin_id": r.IRWINID,
                        "alarm_date": _epoch_ms_to_date(r.ALARM_DATE),
                        "cont_date": _epoch_ms_to_date(r.CONT_DATE),
                        "cause": int(r.CAUSE) if pd.notna(r.CAUSE) else None,
                        "gis_acres": float(r.GIS_ACRES) if pd.notna(r.GIS_ACRES) else None,
                        "objective": int(r.OBJECTIVE) if pd.notna(r.OBJECTIVE) else None,
                        "complex_name": r.COMPLEX_NAME,
                        "geom": f"SRID=4326;{r.geometry.wkt}",
                    }
                )
            session.bulk_insert_mappings(WildfireEvent, rows)
            session.flush()
            total += len(rows)
            parquet_parts.append(page[[*[c for c in FIELDS if c in page.columns], "geometry"]])
            log.info("calfire_page_persisted", total=total)

        try:
            fetch_layer(
                SOURCE_ID,
                spec.api_url,
                where=f"YEAR_ >= {int(min_year)}",
                out_fields=FIELDS,
                geometry_bbox=bbox,
                batch_callback=persist,
            )
        except SourceUnavailableError as exc:
            session.rollback()
            mark_failed(session, SOURCE_ID, str(exc), unavailable=True)
            return IngestResult(SOURCE_ID, "unavailable", message=str(exc))

        if parquet_parts:
            all_gdf = gpd.GeoDataFrame(pd.concat(parquet_parts, ignore_index=True), crs="EPSG:4326")
            write_parquet(all_gdf, "wildfire_perimeters")
        quality = {
            "min_year_filter": min_year,
            "n_invalid_geometries_dropped": dropped_total,
            "year_min": min(years) if years else None,
            "year_max": max(years) if years else None,
            "n_missing_year": total - len(years),
        }
        version = f"frap-{datetime.now(UTC).date().isoformat()}"
        mark_ingested(session, SOURCE_ID, record_count=total, version=version, extra=quality)
        return IngestResult(SOURCE_ID, "ingested", total, version, quality=quality)

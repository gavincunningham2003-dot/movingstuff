"""Linear hazard features (CGS Quaternary faults) and USGS earthquake events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import geopandas as gpd
import pandas as pd
from sqlalchemy import delete
from sqlalchemy.orm import Session

from catrix.config import GEOGRAPHIC_CRS
from catrix.db.models import EarthquakeEvent, HazardLine
from catrix.ingest.arcgis import fetch_layer
from catrix.ingest.base import IngestResult
from catrix.ingest.http import SourceUnavailableError, get_json
from catrix.ingest.registry import load_registry, mark_failed, mark_ingested
from catrix.ingest.store import to_multilinestring, write_parquet
from catrix.logging import get_logger

log = get_logger(__name__)

FAULTS_ID = "cgs_qfaults"
FAULT_FIELDS = [
    "OBJECTID",
    "FLT_NAME",
    "QFLT_ID",
    "ZN_NAME",
    "FLT_AGE",
    "LTYPE",
    "SECT_NAME",
    "FLT_SOURCE",
]
EVENTS_ID = "usgs_earthquake_catalog"


class FaultsConnector:
    source_id = FAULTS_ID

    def run(self, session: Session, *, force: bool = False, **_: Any) -> IngestResult:
        spec = load_registry().get(FAULTS_ID)
        assert spec.api_url
        try:
            gdf = fetch_layer(FAULTS_ID, spec.api_url, out_fields=FAULT_FIELDS)
        except SourceUnavailableError as exc:
            mark_failed(session, FAULTS_ID, str(exc), unavailable=True)
            return IngestResult(FAULTS_ID, "unavailable", message=str(exc))
        gdf["geometry"] = gdf.geometry.apply(to_multilinestring)
        gdf = gdf[gdf.geometry.notna()]
        session.execute(delete(HazardLine).where(HazardLine.hazard_id == "fault"))
        rows = [
            {
                "source_id": FAULTS_ID,
                "hazard_id": "fault",
                "name": r.FLT_NAME,
                "attributes": {k: getattr(r, k) for k in FAULT_FIELDS if k != "OBJECTID"},
                "geom": f"SRID=4326;{r.geometry.wkt}",
            }
            for r in gdf.itertuples()
        ]
        session.bulk_insert_mappings(HazardLine, rows)
        session.flush()
        write_parquet(gdf, "hazard_faults")
        version = f"fam-{datetime.now(UTC).date().isoformat()}"
        mark_ingested(session, FAULTS_ID, record_count=len(rows), version=version)
        return IngestResult(FAULTS_ID, "ingested", len(rows), version)


class EarthquakeEventsConnector:
    source_id = EVENTS_ID

    def run(self, session: Session, *, force: bool = False, **_: Any) -> IngestResult:
        spec = load_registry().get(EVENTS_ID)
        assert spec.api_url
        try:
            data = get_json(EVENTS_ID, spec.api_url, timeout=180)
        except SourceUnavailableError as exc:
            mark_failed(session, EVENTS_ID, str(exc), unavailable=True)
            return IngestResult(EVENTS_ID, "unavailable", message=str(exc))
        feats = data.get("features", [])
        session.execute(delete(EarthquakeEvent).where(EarthquakeEvent.source_id == EVENTS_ID))
        rows = []
        for f in feats:
            p = f.get("properties") or {}
            g = f.get("geometry") or {}
            c = g.get("coordinates") or [None, None, None]
            if c[0] is None or p.get("mag") is None or p.get("time") is None:
                continue
            rows.append(
                {
                    "id": f.get("id"),
                    "source_id": EVENTS_ID,
                    "time": datetime.fromtimestamp(p["time"] / 1000, tz=UTC),
                    "magnitude": float(p["mag"]),
                    "depth_km": float(c[2]) if len(c) > 2 and c[2] is not None else None,
                    "place": p.get("place"),
                    "geom": f"SRID=4326;POINT({c[0]} {c[1]})",
                }
            )
        session.bulk_insert_mappings(EarthquakeEvent, rows)
        session.flush()
        df = pd.DataFrame([{k: v for k, v in r.items() if k != "geom"} for r in rows])
        write_parquet(df, "earthquake_events")
        version = f"comcat-{datetime.now(UTC).date().isoformat()}"
        mark_ingested(session, EVENTS_ID, record_count=len(rows), version=version)
        return IngestResult(EVENTS_ID, "ingested", len(rows), version)


def faults_gdf(session: Session) -> gpd.GeoDataFrame:
    from sqlalchemy import select

    return gpd.read_postgis(
        select(HazardLine.id, HazardLine.name, HazardLine.geom).where(
            HazardLine.hazard_id == "fault"
        ),
        session.connection(),
        geom_col="geom",
        crs=GEOGRAPHIC_CRS,
    ).rename_geometry("geometry")

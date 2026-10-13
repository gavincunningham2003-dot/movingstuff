"""FEMA National Risk Index county table → ``regional_indices`` (context only)."""

from __future__ import annotations

import zipfile
from typing import Any

import pandas as pd
from sqlalchemy import delete
from sqlalchemy.orm import Session

from catrix.db.models import RegionalIndex
from catrix.ingest.base import IngestResult
from catrix.ingest.http import SourceUnavailableError, download_file, get_json
from catrix.ingest.registry import load_registry, mark_failed, mark_ingested
from catrix.ingest.store import write_parquet
from catrix.logging import get_logger

log = get_logger(__name__)

SOURCE_ID = "fema_nri_counties"
# NRI hazard prefixes → CATRIX hazard ids for regional benchmarking
METRICS = {
    "WFIR": "wildfire",
    "ERQK": "earthquake",
    "RFLD": "flood_riverine",  # NRI 1.x name
    "IFLD": "flood_riverine",  # NRI 2.0 name ("Inland Flooding"); whichever is present is used
    "CFLD": "coastal_flooding",
    "DRGT": "drought",
    "HWAV": "extreme_heat",
    "SWND": "windstorm",
    "LNDS": "landslide",
    "TSUN": "tsunami",
    "WNTW": "winter_storm",
}


def _find_local() -> list:
    spec = load_registry().get(SOURCE_ID)
    if not spec.raw_dir.exists():
        return []
    return sorted(spec.raw_dir.glob("*.csv")) + sorted(spec.raw_dir.glob("*.zip"))


def _fetch_feature_service(api_url: str) -> pd.DataFrame:
    """Read the California rows of the NRI county table from FEMA's ArcGIS Online feature
    service (one attribute row per county, same column names as the CSV release)."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        data = get_json(
            SOURCE_ID,
            f"{api_url}/query",
            params={
                "where": "STATEABBRV='CA'",
                "outFields": "*",
                "returnGeometry": "false",
                "resultOffset": offset,
                "resultRecordCount": 1000,
                "f": "json",
            },
        )
        if "error" in data:
            raise SourceUnavailableError(SOURCE_ID, api_url, str(data["error"]))
        feats = data.get("features", [])
        rows.extend(f["attributes"] for f in feats)
        if not data.get("exceededTransferLimit") or not feats:
            break
        offset += len(feats)
    if not rows:
        raise SourceUnavailableError(SOURCE_ID, api_url, "feature service returned no rows")
    df = pd.DataFrame(rows)
    for col in ("STCOFIPS", "STATEFIPS", "COUNTYFIPS"):
        if col in df.columns:
            df[col] = df[col].astype(str)
    return df


class NRIConnector:
    source_id = SOURCE_ID

    def run(self, session: Session, *, force: bool = False, **_: Any) -> IngestResult:
        spec = load_registry().get(SOURCE_ID)
        assert spec.download_url
        path = None
        local = _find_local()
        if local and not force:
            path = local[0]
        else:
            try:
                path = download_file(
                    SOURCE_ID, spec.download_url, force=force, expected_min_bytes=100_000
                ).path
            except SourceUnavailableError as exc:
                if local:
                    path = local[0]
                elif spec.api_url:
                    log.warning("nri_zip_unavailable_using_feature_service", error=str(exc))
                    path = None
                else:
                    mark_failed(session, SOURCE_ID, str(exc), unavailable=True)
                    return IngestResult(SOURCE_ID, "unavailable", message=str(exc))
        try:
            if path is None:
                assert spec.api_url
                df = _fetch_feature_service(spec.api_url)
            elif str(path).endswith(".zip"):
                with zipfile.ZipFile(path) as zf:
                    csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                    if not csvs:
                        raise SourceUnavailableError(SOURCE_ID, str(path), "zip contains no CSV")
                    with zf.open(csvs[0]) as fh:
                        df = pd.read_csv(
                            fh, dtype={"STCOFIPS": str, "STATEFIPS": str, "COUNTYFIPS": str}
                        )
            else:
                df = pd.read_csv(path, dtype={"STCOFIPS": str, "STATEFIPS": str, "COUNTYFIPS": str})
        except Exception as exc:
            mark_failed(session, SOURCE_ID, f"parse failed: {exc!r}")
            return IngestResult(SOURCE_ID, "failed", message=str(exc))
        if "STCOFIPS" not in df.columns:
            msg = "NRI table missing STCOFIPS column (schema drift)"
            mark_failed(session, SOURCE_ID, msg)
            return IngestResult(SOURCE_ID, "failed", message=msg)
        ca = df[df["STCOFIPS"].str.startswith("06")].copy()
        version = str(ca["NRI_VER"].iloc[0]) if "NRI_VER" in ca.columns and len(ca) else None
        session.execute(delete(RegionalIndex).where(RegionalIndex.source_id == SOURCE_ID))
        rows = []
        for r in ca.itertuples():
            fips = r.STCOFIPS.zfill(5)
            for prefix, hazard in METRICS.items():
                score_col, rating_col, eal_col = (
                    f"{prefix}_RISKS",
                    f"{prefix}_RISKR",
                    f"{prefix}_EALT",
                )
                if score_col in ca.columns:
                    val = getattr(r, score_col)
                    rows.append(
                        {
                            "source_id": SOURCE_ID,
                            "county_fips": fips,
                            "metric": f"{hazard}_risk_score",
                            "value": None if pd.isna(val) else float(val),
                            "rating": getattr(r, rating_col, None)
                            if rating_col in ca.columns
                            else None,
                            "version": version,
                        }
                    )
                if eal_col in ca.columns:
                    val = getattr(r, eal_col)
                    rows.append(
                        {
                            "source_id": SOURCE_ID,
                            "county_fips": fips,
                            "metric": f"{hazard}_eal_total_usd",
                            "value": None if pd.isna(val) else float(val),
                            "rating": None,
                            "version": version,
                        }
                    )
            if "RISK_SCORE" in ca.columns:
                rows.append(
                    {
                        "source_id": SOURCE_ID,
                        "county_fips": fips,
                        "metric": "composite_risk_score",
                        "value": None if pd.isna(r.RISK_SCORE) else float(r.RISK_SCORE),
                        "rating": getattr(r, "RISK_RATNG", None)
                        if "RISK_RATNG" in ca.columns
                        else None,
                        "version": version,
                    }
                )
        session.bulk_insert_mappings(RegionalIndex, rows)
        session.flush()
        write_parquet(pd.DataFrame(rows), "nri_counties_ca")
        mark_ingested(
            session,
            SOURCE_ID,
            record_count=len(rows),
            version=version,
            extra={"n_counties": int(ca["STCOFIPS"].nunique())},
        )
        return IngestResult(SOURCE_ID, "ingested", len(rows), version)

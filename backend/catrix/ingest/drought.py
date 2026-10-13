"""US Drought Monitor county statistics → yearly drought features per county.

Weekly rows (verified CSV) are aggregated per calendar year:

    drought_d2_share   mean weekly % of county area in D2 (severe) or worse
    drought_d3_max     max weekly % of county area in D3 (extreme) or worse
    drought_weeks_d2   number of weeks with any D2+ area

Output: ``data/processed/ml/drought_features.parquet`` keyed (county_fips, feature_year).
USDM begins 2000-01-04; earlier years have no row and are left null downstream.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from catrix.db.models import Address
from catrix.ingest.base import IngestResult
from catrix.ingest.http import SourceUnavailableError, get_text
from catrix.ingest.registry import load_registry, mark_failed, mark_ingested
from catrix.ingest.store import write_parquet
from catrix.logging import get_logger

log = get_logger(__name__)

SOURCE_ID = "us_drought_monitor"
REQUIRED = ["MapDate", "FIPS", "D2", "D3"]


def parse_usdm_csv(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text), dtype={"FIPS": str})
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise SourceUnavailableError(SOURCE_ID, "usdm", f"schema drift: missing {missing}")
    df["year"] = df["MapDate"].astype(str).str[:4].astype(int)
    return df


def yearly_drought_features(weekly: pd.DataFrame) -> pd.DataFrame:
    g = weekly.groupby(["FIPS", "year"])
    out = pd.DataFrame(
        {
            "drought_d2_share": g["D2"].mean(),
            "drought_d3_max": g["D3"].max(),
            "drought_weeks_d2": g["D2"].apply(lambda s: int((s > 0).sum())),
        }
    ).reset_index()
    return out.rename(columns={"FIPS": "county_fips", "year": "feature_year"})


class DroughtConnector:
    source_id = SOURCE_ID

    def run(self, session: Session, *, force: bool = False, **_: Any) -> IngestResult:
        spec = load_registry().get(SOURCE_ID)
        assert spec.api_url
        fips_list = [
            f for (f,) in session.execute(select(distinct(Address.county_fips))).all() if f
        ]
        if not fips_list:
            mark_failed(session, SOURCE_ID, "no addresses with county")
            return IngestResult(SOURCE_ID, "failed", message="no addresses with county")
        end = datetime.now(UTC)
        parts: list[pd.DataFrame] = []
        failed: list[str] = []
        for fips in sorted(fips_list):
            url = spec.api_url.format(
                fips=fips, start="1/1/2000", end=f"{end.month}/{end.day}/{end.year}"
            )
            try:
                parts.append(parse_usdm_csv(get_text(SOURCE_ID, url)))
                log.info("usdm_county_fetched", fips=fips)
            except SourceUnavailableError as exc:
                failed.append(fips)
                log.error("usdm_county_failed", fips=fips, error=str(exc))
        if not parts:
            mark_failed(session, SOURCE_ID, "no USDM data retrieved", unavailable=True)
            return IngestResult(SOURCE_ID, "unavailable", message="no USDM data retrieved")
        feats = yearly_drought_features(pd.concat(parts, ignore_index=True))
        # Drop the current (incomplete) year so partial-year means never enter the features.
        feats = feats[feats["feature_year"] < end.year]
        write_parquet(feats, "drought_features", subdir="ml")
        quality = {
            "counties": len(fips_list),
            "failed_counties": failed,
            "years": [int(feats["feature_year"].min()), int(feats["feature_year"].max())],
        }
        version = f"usdm-{end.date().isoformat()}"
        mark_ingested(session, SOURCE_ID, record_count=len(feats), version=version, extra=quality)
        return IngestResult(SOURCE_ID, "ingested", len(feats), version, quality=quality)

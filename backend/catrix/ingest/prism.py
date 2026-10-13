"""PRISM annual precipitation and maximum temperature → per-address climate features.

Downloads CONUS annual grids for ``ppt`` and ``tmax`` (1991 → latest complete year), samples
every address, and derives anomalies against the 1991-2020 mean of the same grids:

    precip_mm, precip_pct_normal, precip_prev_year_pct_normal, precip_3y_pct_normal,
    tmax_c, tmax_anomaly_c

Output: ``data/processed/ml/climate_features.parquet`` keyed (address_id, feature_year).
Values for year T are observations of year T, so they are knowable by 31 December of T.
"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import rasterio
from sqlalchemy import select
from sqlalchemy.orm import Session

from catrix.db.models import Address
from catrix.geo.raster import sample_values
from catrix.ingest.base import IngestResult
from catrix.ingest.http import SourceUnavailableError, download_file
from catrix.ingest.registry import load_registry, mark_failed, mark_ingested
from catrix.ingest.store import write_parquet
from catrix.logging import get_logger

log = get_logger(__name__)

SOURCE_ID = "prism_climate"
ELEMENTS = ("ppt", "tmax")
NORMAL_YEARS = (1991, 2020)


def _extract_raster(zip_path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        raster = next((n for n in names if n.lower().endswith((".tif", ".bil"))), None)
        if raster is None:
            raise SourceUnavailableError(
                SOURCE_ID, str(zip_path), "zip contains no .tif/.bil raster"
            )
        for n in names:  # BIL needs its sidecars (.hdr/.prj/.stx)
            zf.extract(n, dest_dir)
    return dest_dir / raster


def fetch_annual_grid(element: str, year: int, *, force: bool = False) -> Path:
    spec = load_registry().get(SOURCE_ID)
    assert spec.api_url
    url = spec.api_url.format(element=element, year=year)
    dest = spec.raw_dir / f"{element}_{year}.zip"
    dl = download_file(SOURCE_ID, url, dest=dest, force=force, expected_min_bytes=100_000)
    return _extract_raster(dl.path, spec.raw_dir / f"{element}_{year}")


def sample_grid(path: Path, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    with rasterio.open(path) as ds:
        return sample_values(ds, lons, lats)


def derive_climate_features(samples: pd.DataFrame) -> pd.DataFrame:
    """``samples`` columns: address_id, year, ppt, tmax (one row per address-year)."""
    df = samples.sort_values(["address_id", "year"]).copy()
    base = df[(df["year"] >= NORMAL_YEARS[0]) & (df["year"] <= NORMAL_YEARS[1])]
    normals = (
        base.groupby("address_id")[["ppt", "tmax"]]
        .mean()
        .rename(columns={"ppt": "ppt_normal", "tmax": "tmax_normal"})
    )
    df = df.merge(normals, on="address_id", how="left")
    df["precip_mm"] = df["ppt"]
    df["precip_pct_normal"] = 100.0 * df["ppt"] / df["ppt_normal"]
    df["tmax_c"] = df["tmax"]
    df["tmax_anomaly_c"] = df["tmax"] - df["tmax_normal"]
    g = df.groupby("address_id")["precip_pct_normal"]
    df["precip_prev_year_pct_normal"] = g.shift(1)
    df["precip_3y_pct_normal"] = g.transform(lambda s: s.rolling(3, min_periods=2).mean())
    df = df.rename(columns={"year": "feature_year"})
    return df[
        [
            "address_id",
            "feature_year",
            "precip_mm",
            "precip_pct_normal",
            "precip_prev_year_pct_normal",
            "precip_3y_pct_normal",
            "tmax_c",
            "tmax_anomaly_c",
        ]
    ]


class PRISMConnector:
    source_id = SOURCE_ID

    def run(
        self,
        session: Session,
        *,
        force: bool = False,
        start_year: int = 1991,
        end_year: int | None = None,
        **_: Any,
    ) -> IngestResult:
        rows = session.execute(select(Address.id, Address.lon, Address.lat)).all()
        if not rows:
            mark_failed(session, SOURCE_ID, "no addresses ingested")
            return IngestResult(SOURCE_ID, "failed", message="no addresses ingested")
        ids = np.array([r[0] for r in rows])
        lons = np.array([r[1] for r in rows], dtype=float)
        lats = np.array([r[2] for r in rows], dtype=float)
        last = end_year or (datetime.now(UTC).year - 1)
        parts: list[pd.DataFrame] = []
        failed: list[str] = []
        for year in range(start_year, last + 1):
            vals: dict[str, np.ndarray] = {}
            for el in ELEMENTS:
                try:
                    path = fetch_annual_grid(el, year, force=force)
                    vals[el] = sample_grid(path, lons, lats)
                except SourceUnavailableError as exc:
                    failed.append(f"{el}_{year}")
                    log.error("prism_grid_failed", element=el, year=year, error=str(exc))
            if len(vals) == len(ELEMENTS):
                parts.append(
                    pd.DataFrame(
                        {"address_id": ids, "year": year, "ppt": vals["ppt"], "tmax": vals["tmax"]}
                    )
                )
                log.info("prism_year_sampled", year=year)
        if not parts:
            mark_failed(
                session, SOURCE_ID, f"no PRISM grids retrieved ({failed[:5]}...)", unavailable=True
            )
            return IngestResult(SOURCE_ID, "unavailable", message="no PRISM grids retrieved")
        feats = derive_climate_features(pd.concat(parts, ignore_index=True))
        write_parquet(feats, "climate_features", subdir="ml")
        years = sorted(feats["feature_year"].unique().tolist())
        quality = {
            "years": [years[0], years[-1]],
            "n_years": len(years),
            "failed_grids": failed,
            "normal_period": list(NORMAL_YEARS),
        }
        mark_ingested(
            session,
            SOURCE_ID,
            record_count=len(feats),
            version=f"prism-4km-{years[-1]}",
            extra=quality,
        )
        return IngestResult(
            SOURCE_ID, "ingested", len(feats), f"prism-4km-{years[-1]}", quality=quality
        )

"""LANDFIRE fuel model (FBFM40) and canopy cover (CC) by version → per-address fuel features.

Acquisition
-----------
* **LFPS job** (verified service; versions LF2016, LF2022, LF2023, LF2024): submit
  ``Layer_List=<ver>_FBFM40;<ver>_CC`` with ``Area_of_Interest="W S E N"`` (WGS84), poll the
  job, download the zip, store under ``data/raw/landfire_fuels/<ver>/``.
* **Local versions**: any ``data/raw/landfire_fuels/LF<year>/*.tif`` (e.g. manually
  downloaded LF2001/2008/2010/2012/2014) is used as well.

Features per (address, version): ``fbfm40`` code, ``fuel_group`` (NB=0, GR=1, GS=2, SH=3,
TU=4, TL=5, SB=6), ``fuel_burnable`` (0/1), ``canopy_cover_pct`` and
``burnable_frac_500m`` (share of burnable pixels within a 500 m window — a wildland-urban
interface proxy). Output ``data/processed/ml/fuel_features.parquet``; the dataset builder
maps each training year to the latest version with data year ≤ T.
"""

from __future__ import annotations

import re
import time
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import rasterio
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from catrix.config import get_settings
from catrix.db.models import Address
from catrix.geo.raster import sample_values, window_fraction
from catrix.ingest.base import IngestResult
from catrix.ingest.http import SourceUnavailableError, download_file, get_json
from catrix.ingest.registry import load_registry, mark_failed, mark_ingested
from catrix.ingest.store import write_parquet
from catrix.logging import get_logger

log = get_logger(__name__)

SOURCE_ID = "landfire_fuels"
LFPS_VERSIONS = ("LF2016", "LF2022", "LF2023", "LF2024")
# Data year represented by each LANDFIRE version (vegetation/fuels currency).
VERSION_DATA_YEAR = {
    "LF2001": 2001,
    "LF2008": 2008,
    "LF2010": 2010,
    "LF2012": 2012,
    "LF2014": 2014,
    "LF2016": 2016,
    "LF2019": 2019,
    "LF2020": 2020,
    "LF2022": 2022,
    "LF2023": 2023,
    "LF2024": 2024,
    "LF2025": 2025,
}
FUEL_GROUPS = [
    (91, 99, 0),
    (101, 109, 1),
    (121, 124, 2),
    (141, 149, 3),
    (161, 165, 4),
    (181, 189, 5),
    (201, 204, 6),
]
POLL_SECONDS = 15
MAX_WAIT_SECONDS = 3600


def fuel_group(code: int | None) -> int | None:
    if code is None:
        return None
    for lo, hi, g in FUEL_GROUPS:
        if lo <= code <= hi:
            return g
    return None


def is_burnable(code: int | None) -> int | None:
    g = fuel_group(code)
    return None if g is None else int(g > 0)


# --------------------------------------------------------------------------- LFPS client
def submit_lfps_job(version: str, bbox: tuple[float, float, float, float]) -> str:
    spec = load_registry().get(SOURCE_ID)
    assert spec.api_url
    email = get_settings().landfire_email
    if not email:
        raise SourceUnavailableError(
            SOURCE_ID,
            spec.api_url,
            "LFPS requires a contact e-mail per job; set CATRIX_LANDFIRE_EMAIL",
        )
    minx, miny, maxx, maxy = bbox
    params = {
        "Email": email,
        "Layer_List": f"{version}_FBFM40;{version}_CC",
        "Area_of_Interest": f"{minx} {miny} {maxx} {maxy}",
        "Output_Projection": "5070",
    }
    data = get_json(SOURCE_ID, f"{spec.api_url}/job/submit", params=params)
    job_id = data.get("jobId")
    if not job_id:
        raise SourceUnavailableError(
            SOURCE_ID, spec.api_url, f"job submit returned no jobId: {data}"
        )
    return str(job_id)


def wait_for_lfps_job(job_id: str) -> str:
    """Poll until the job succeeds; return the output zip URL."""
    spec = load_registry().get(SOURCE_ID)
    assert spec.api_url
    waited = 0
    while waited < MAX_WAIT_SECONDS:
        status = get_json(SOURCE_ID, f"{spec.api_url}/job/status", params={"JobId": job_id})
        st = str(status.get("status", ""))
        if st == "Succeeded":
            url = status.get("outputFile")
            if not url:
                raise SourceUnavailableError(
                    SOURCE_ID, spec.api_url, f"job {job_id} has no outputFile"
                )
            return str(url)
        if st in ("Failed", "Cancelled", "TimedOut", "Error"):
            msgs = [m.get("description") for m in status.get("messages", [])][-3:]
            raise SourceUnavailableError(
                SOURCE_ID, spec.api_url, f"job {job_id} ended with {st}: {msgs}"
            )
        time.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
    raise SourceUnavailableError(
        SOURCE_ID, spec.api_url, f"job {job_id} did not finish within {MAX_WAIT_SECONDS}s"
    )


def fetch_version(
    version: str, bbox: tuple[float, float, float, float], *, force: bool = False
) -> Path:
    spec = load_registry().get(SOURCE_ID)
    out_dir = spec.raw_dir / version
    existing = sorted(out_dir.glob("*.tif")) if out_dir.exists() else []
    if existing and not force:
        return out_dir
    job = submit_lfps_job(version, bbox)
    log.info("lfps_job_submitted", version=version, job=job)
    url = wait_for_lfps_job(job)
    dl = download_file(
        SOURCE_ID, url, dest=spec.raw_dir / f"{version}.zip", force=True, expected_min_bytes=10_000
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dl.path) as zf:
        zf.extractall(out_dir)
    return out_dir


def available_versions() -> dict[str, Path]:
    spec = load_registry().get(SOURCE_ID)
    if not spec.raw_dir.exists():
        return {}
    out = {}
    for d in sorted(spec.raw_dir.iterdir()):
        if d.is_dir() and re.fullmatch(r"LF\d{4}", d.name) and list(d.glob("*.tif")):
            out[d.name] = d
    return out


def _band_index(path: Path, keyword: str) -> int | None:
    with rasterio.open(path) as ds:
        for i, desc in enumerate(ds.descriptions, start=1):
            if desc and keyword.lower() in desc.lower():
                return i
    return None


def find_layer(version_dir: Path, keyword: str) -> tuple[Path, int] | None:
    """Locate the raster+band holding FBFM40 or CC: either a single-band file whose name
    contains the keyword, or a band of the multi-band LFPS output."""
    for tif in sorted(version_dir.glob("*.tif")):
        if keyword.lower() in tif.name.lower():
            return tif, 1
    for tif in sorted(version_dir.glob("*.tif")):
        b = _band_index(tif, keyword)
        if b is not None:
            return tif, b
    tifs = sorted(version_dir.glob("*.tif"))
    if tifs:  # LFPS multi-band order follows Layer_List: FBFM40 first, CC second
        with rasterio.open(tifs[0]) as ds:
            if ds.count >= 2:
                return tifs[0], 1 if keyword == "FBFM40" else 2
    return None


def sample_version(
    version: str, version_dir: Path, ids: np.ndarray, lons: np.ndarray, lats: np.ndarray
) -> pd.DataFrame | None:
    fb = find_layer(version_dir, "FBFM40")
    if fb is None:
        log.warning("landfire_fbfm40_missing", version=version)
        return None
    with rasterio.open(fb[0]) as ds:
        codes = sample_values(ds, lons, lats, band=fb[1])
        burn = window_fraction(
            ds, lons, lats, band=fb[1], radius_m=500.0, predicate=lambda a: (a >= 101) & (a <= 204)
        )
    cc_vals = np.full(len(ids), np.nan)
    cc = find_layer(version_dir, "CC")
    if cc is not None and cc != fb:
        with rasterio.open(cc[0]) as ds:
            cc_vals = sample_values(ds, lons, lats, band=cc[1])
    code_int = [None if np.isnan(c) else int(c) for c in codes]
    return pd.DataFrame(
        {
            "address_id": ids,
            "lf_version": version,
            "data_year": VERSION_DATA_YEAR.get(version, int(version[2:])),
            "fbfm40": [c if c is None else c for c in code_int],
            "fuel_group": [fuel_group(c) for c in code_int],
            "fuel_burnable": [is_burnable(c) for c in code_int],
            "canopy_cover_pct": cc_vals,
            "burnable_frac_500m": burn,
        }
    )


class LandfireConnector:
    source_id = SOURCE_ID

    def run(
        self, session: Session, *, force: bool = False, versions: list[str] | None = None, **_: Any
    ) -> IngestResult:
        rows = session.execute(select(Address.id, Address.lon, Address.lat)).all()
        if not rows:
            mark_failed(session, SOURCE_ID, "no addresses ingested")
            return IngestResult(SOURCE_ID, "failed", message="no addresses ingested")
        bb = session.execute(
            select(
                func.min(Address.lon),
                func.min(Address.lat),
                func.max(Address.lon),
                func.max(Address.lat),
            )
        ).one()
        bbox = (bb[0] - 0.02, bb[1] - 0.02, bb[2] + 0.02, bb[3] + 0.02)
        failed: list[str] = []
        for v in versions or LFPS_VERSIONS:
            try:
                fetch_version(v, bbox, force=force)
            except SourceUnavailableError as exc:
                failed.append(v)
                log.error("landfire_version_failed", version=v, error=str(exc))
        avail = available_versions()
        if not avail:
            mark_failed(
                session,
                SOURCE_ID,
                f"no LANDFIRE rasters available (failed: {failed})",
                unavailable=True,
            )
            return IngestResult(
                SOURCE_ID,
                "unavailable",
                message=f"no LANDFIRE rasters available (failed: {failed})",
            )
        ids = np.array([r[0] for r in rows])
        lons = np.array([r[1] for r in rows], dtype=float)
        lats = np.array([r[2] for r in rows], dtype=float)
        parts = [
            df
            for v, d in avail.items()
            if (df := sample_version(v, d, ids, lons, lats)) is not None
        ]
        feats = pd.concat(parts, ignore_index=True)
        write_parquet(feats, "fuel_features", subdir="ml")
        quality = {
            "versions": sorted(avail),
            "failed_versions": failed,
            "bbox": bbox,
            "burnable_share": float(feats["fuel_burnable"].dropna().mean())
            if feats["fuel_burnable"].notna().any()
            else None,
        }
        mark_ingested(
            session,
            SOURCE_ID,
            record_count=len(feats),
            version=",".join(sorted(avail)),
            extra=quality,
        )
        return IngestResult(
            SOURCE_ID, "ingested", len(feats), ",".join(sorted(avail)), quality=quality
        )

"""MRLC NLCD land cover epochs via WCS → per-address land-cover features.

For each 0.5-degree tile containing sampled addresses and each epoch (2001 … 2021) a
``GetCoverage`` request (verified service, EPSG:5070, axes X/Y) retrieves a GeoTIFF clip.
Features per (address, epoch): ``lc_code``, ``lc_developed_intensity`` (0 = not developed,
21→1 … 24→4), ``lc_veg_frac_500m`` (forest/shrub/grass share within 500 m) and
``lc_developed_frac_500m``. Output ``data/processed/ml/landcover_features.parquet``.

Annual NLCD bundles placed manually under ``data/raw/mrlc_nlcd/<year>/*.tif`` are used in
addition to (and take precedence over) WCS epochs for the same year.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from sqlalchemy import select
from sqlalchemy.orm import Session

from catrix.db.models import Address
from catrix.geo.raster import sample_values, window_fraction
from catrix.ingest.base import IngestResult
from catrix.ingest.http import SourceUnavailableError, download_file
from catrix.ingest.registry import load_registry, mark_failed, mark_ingested
from catrix.ingest.store import write_parquet
from catrix.logging import get_logger

log = get_logger(__name__)

SOURCE_ID = "mrlc_nlcd"
EPOCHS = (2001, 2004, 2006, 2008, 2011, 2013, 2016, 2019, 2021)
TILE_DEG = 0.5
VEG_CODES = (41, 42, 43, 52, 71, 90, 95)
DEVELOPED = {21: 1, 22: 2, 23: 3, 24: 4}


def tile_id(lon: float, lat: float) -> str:
    return f"{math.floor(lon / TILE_DEG)}_{math.floor(lat / TILE_DEG)}"


def tile_bbox_5070(tid: str, pad_m: float = 1000.0) -> tuple[float, float, float, float]:
    tx, ty = (int(v) for v in tid.split("_"))
    minx, miny = tx * TILE_DEG, ty * TILE_DEG
    maxx, maxy = minx + TILE_DEG, miny + TILE_DEG
    t = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    xs, ys = t.transform([minx, maxx, minx, maxx], [miny, miny, maxy, maxy])
    return (min(xs) - pad_m, min(ys) - pad_m, max(xs) + pad_m, max(ys) + pad_m)


def wcs_url(year: int, bbox_5070: tuple[float, float, float, float]) -> str:
    spec = load_registry().get(SOURCE_ID)
    assert spec.api_url
    minx, miny, maxx, maxy = (round(v, 1) for v in bbox_5070)
    return spec.api_url.format(year=year, minx=minx, maxx=maxx, miny=miny, maxy=maxy)


def fetch_tile(year: int, tid: str, *, force: bool = False) -> Path:
    spec = load_registry().get(SOURCE_ID)
    dest = spec.raw_dir / "wcs" / f"nlcd_{year}_{tid}.tif"
    dl = download_file(
        SOURCE_ID,
        wcs_url(year, tile_bbox_5070(tid)),
        dest=dest,
        force=force,
        expected_min_bytes=1000,
    )
    with rasterio.open(dl.path) as ds:  # validate it is a raster, not an XML error document
        if ds.count < 1:
            raise SourceUnavailableError(SOURCE_ID, dl.url, "empty coverage")
    return dl.path


def local_years() -> dict[int, Path]:
    spec = load_registry().get(SOURCE_ID)
    out: dict[int, Path] = {}
    if not spec.raw_dir.exists():
        return out
    for d in spec.raw_dir.iterdir():
        if d.is_dir() and d.name.isdigit():
            tifs = sorted(d.glob("*.tif"))
            if tifs:
                out[int(d.name)] = tifs[0]
    return out


def features_from_raster(
    path: Path, ids: np.ndarray, lons: np.ndarray, lats: np.ndarray, year: int
) -> pd.DataFrame:
    with rasterio.open(path) as ds:
        codes = sample_values(ds, lons, lats)
        veg = window_fraction(
            ds, lons, lats, radius_m=500.0, predicate=lambda a: np.isin(a, VEG_CODES)
        )
        dev = window_fraction(
            ds, lons, lats, radius_m=500.0, predicate=lambda a: (a >= 21) & (a <= 24)
        )
    code_int = [None if np.isnan(c) else int(c) for c in codes]
    return pd.DataFrame(
        {
            "address_id": ids,
            "nlcd_year": year,
            "lc_code": code_int,
            "lc_developed_intensity": [
                None if c is None else DEVELOPED.get(c, 0) for c in code_int
            ],
            "lc_veg_frac_500m": veg,
            "lc_developed_frac_500m": dev,
        }
    )


class NLCDConnector:
    source_id = SOURCE_ID

    def run(
        self, session: Session, *, force: bool = False, epochs: list[int] | None = None, **_: Any
    ) -> IngestResult:
        rows = session.execute(select(Address.id, Address.lon, Address.lat)).all()
        if not rows:
            mark_failed(session, SOURCE_ID, "no addresses ingested")
            return IngestResult(SOURCE_ID, "failed", message="no addresses ingested")
        df = pd.DataFrame(rows, columns=["address_id", "lon", "lat"])
        df["tile"] = [tile_id(lo, la) for lo, la in zip(df["lon"], df["lat"], strict=True)]
        parts: list[pd.DataFrame] = []
        failed: list[str] = []
        local = local_years()
        for year in epochs or EPOCHS:
            if year in local:
                parts.append(
                    features_from_raster(
                        local[year],
                        df["address_id"].to_numpy(),
                        df["lon"].to_numpy(float),
                        df["lat"].to_numpy(float),
                        year,
                    )
                )
                continue
            for tid, g in df.groupby("tile"):
                try:
                    path = fetch_tile(year, str(tid), force=force)
                except SourceUnavailableError as exc:
                    failed.append(f"{year}:{tid}")
                    log.error("nlcd_tile_failed", year=year, tile=tid, error=str(exc))
                    continue
                parts.append(
                    features_from_raster(
                        path,
                        g["address_id"].to_numpy(),
                        g["lon"].to_numpy(float),
                        g["lat"].to_numpy(float),
                        year,
                    )
                )
            log.info("nlcd_epoch_sampled", year=year)
        for year, path in local.items():
            if year not in (epochs or EPOCHS):
                parts.append(
                    features_from_raster(
                        path,
                        df["address_id"].to_numpy(),
                        df["lon"].to_numpy(float),
                        df["lat"].to_numpy(float),
                        year,
                    )
                )
        if not parts:
            mark_failed(
                session, SOURCE_ID, f"no NLCD tiles retrieved ({failed[:5]}...)", unavailable=True
            )
            return IngestResult(SOURCE_ID, "unavailable", message="no NLCD tiles retrieved")
        feats = pd.concat(parts, ignore_index=True).drop_duplicates(["address_id", "nlcd_year"])
        write_parquet(feats, "landcover_features", subdir="ml")
        years = sorted(feats["nlcd_year"].unique().tolist())
        quality = {"years": years, "failed_tiles": failed, "n_tiles": int(df["tile"].nunique())}
        mark_ingested(
            session,
            SOURCE_ID,
            record_count=len(feats),
            version=f"nlcd-{years[0]}-{years[-1]}",
            extra=quality,
        )
        return IngestResult(
            SOURCE_ID, "ingested", len(feats), f"nlcd-{years[0]}-{years[-1]}", quality=quality
        )

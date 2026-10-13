"""Static features for ad-hoc points (portfolio uploads, API predictions).

The training table takes elevation, slope, aspect and building density from the
``environmental_observations`` and ``address_buildings`` tables, which exist only for the
ingested address sample. Uploaded portfolio locations have no rows there, so without this
module the model would see NaN for five of its features, including elevation, its single
strongest one. This computes the same five columns on the fly:

* terrain from the USGS 3DEP 1-arc-second tiles already cached by the elevation connector
  (missing tiles are downloaded on demand, up to ``max_tiles`` per call);
* building density as the count of Microsoft footprints within 100 m, from PostGIS.

Columns: ``elevation_m``, ``slope_deg``, ``northness``, ``eastness``, ``buildings_within_100m``.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from catrix.geo.raster import dem_tile_id, open_raster, sample_terrain
from catrix.ingest.http import SourceUnavailableError, download_file
from catrix.ingest.registry import load_registry
from catrix.ingest.store import chunked
from catrix.logging import get_logger

log = get_logger(__name__)

DEM_SOURCE = "usgs_3dep_1as"
STATIC_COLS = ["elevation_m", "slope_deg", "northness", "eastness", "buildings_within_100m"]
BATCH = 500


def terrain_for_points(points: pd.DataFrame, *, max_tiles: int = 40) -> pd.DataFrame:
    """Elevation / slope / aspect-derived northness & eastness per row of ``points``
    (columns ``lon``, ``lat``). Rows whose tile cannot be obtained are NaN."""
    spec = load_registry().get(DEM_SOURCE)
    assert spec.download_pattern
    by_tile: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for i, (lon, lat) in enumerate(
        zip(points["lon"].astype(float), points["lat"].astype(float), strict=False)
    ):
        by_tile[dem_tile_id(lon, lat)].append((i, lon, lat))

    elev = np.full(len(points), np.nan)
    slope = np.full(len(points), np.nan)
    aspect = np.full(len(points), np.nan)
    for n, (tile, pts) in enumerate(sorted(by_tile.items())):
        if n >= max_tiles:
            log.warning("terrain_tile_cap", tile=tile, skipped=len(pts))
            continue
        try:
            dl = download_file(
                DEM_SOURCE,
                spec.download_pattern.format(tile=tile),
                dest=spec.raw_dir / f"USGS_1_{tile}.tif",
                expected_min_bytes=1_000_000,
            )
        except SourceUnavailableError as exc:
            log.warning("terrain_tile_unavailable", tile=tile, error=str(exc))
            continue
        with open_raster(dl.path) as ds:
            for i, lon, lat in pts:
                s = sample_terrain(ds, lon, lat)
                if s.elevation_m is not None:
                    elev[i] = s.elevation_m
                if s.slope_deg is not None:
                    slope[i] = s.slope_deg
                if s.aspect_deg is not None and s.aspect_deg >= 0:
                    aspect[i] = s.aspect_deg
    asp = np.radians(aspect)
    return pd.DataFrame(
        {
            "elevation_m": elev,
            "slope_deg": slope,
            "northness": np.cos(asp),
            "eastness": np.sin(asp),
        },
        index=points.index,
    )


def buildings_within_100m(session: Session, points: pd.DataFrame) -> pd.Series:
    """Count Microsoft building footprints within 100 m of each point (PostGIS). A bbox
    pre-filter keeps the plain geometry GiST index usable before the exact geography test."""
    rows = list(
        zip(
            range(len(points)),
            points["lon"].astype(float),
            points["lat"].astype(float),
            strict=True,
        )
    )
    counts = np.zeros(len(points), dtype=int)
    for chunk in chunked(rows, BATCH):
        values = ", ".join(f"({i}, {lon!r}, {lat!r})" for i, lon, lat in chunk)
        sql = text(
            f"""
            WITH pts(i, lon, lat) AS (VALUES {values}),
            p AS (SELECT i, ST_SetSRID(ST_MakePoint(lon, lat), 4326) AS g FROM pts)
            SELECT p.i AS i, count(b.id) AS n
            FROM p
            LEFT JOIN buildings b
              ON b.geom && ST_Expand(p.g, 0.0015)
             AND ST_DWithin(b.geom::geography, p.g::geography, 100)
            GROUP BY p.i
            """
        )
        for r in session.execute(sql).mappings():
            counts[int(r["i"])] = int(r["n"])
    return pd.Series(counts, index=points.index, name="buildings_within_100m")


def static_features_for_points(session: Session, points: pd.DataFrame) -> pd.DataFrame:
    """``points`` with the five static model features attached (existing columns are kept)."""
    out = points.copy()
    missing = [c for c in STATIC_COLS if c not in out.columns]
    if not missing:
        return out
    terrain = terrain_for_points(out)
    for c in ("elevation_m", "slope_deg", "northness", "eastness"):
        if c in missing:
            out[c] = terrain[c].values
    if "buildings_within_100m" in missing:
        out["buildings_within_100m"] = buildings_within_100m(session, out).values
    return out

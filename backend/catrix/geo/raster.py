"""Raster sampling utilities (elevation, slope, aspect, categorical land cover).

Slope and aspect use Horn's (1981) 3x3 finite-difference method. For rasters in a
geographic CRS the pixel spacing is converted to metres at the sample latitude so the
gradient is dimensionally consistent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import rasterio.transform
from pyproj import CRS, Transformer
from rasterio.windows import Window

from catrix.geo.crs import metres_per_degree


@dataclass
class TerrainSample:
    elevation_m: float | None
    slope_deg: float | None
    aspect_deg: float | None
    status: str  # 'ok' | 'nodata' | 'outside' | 'edge'


def _to_raster_crs(ds: rasterio.DatasetReader, lon: float, lat: float) -> tuple[float, float]:
    if ds.crs is None or CRS.from_user_input(ds.crs).to_epsg() == 4326:
        return lon, lat
    t = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
    x, y = t.transform(lon, lat)
    return float(x), float(y)


def horn_slope_aspect(z: np.ndarray, dx_m: float, dy_m: float) -> tuple[float, float]:
    """Slope (degrees) and aspect (degrees clockwise from north) from a 3x3 window."""
    if z.shape != (3, 3):
        raise ValueError("expected a 3x3 window")
    dzdx = ((z[0, 2] + 2 * z[1, 2] + z[2, 2]) - (z[0, 0] + 2 * z[1, 0] + z[2, 0])) / (8 * dx_m)
    dzdy = ((z[2, 0] + 2 * z[2, 1] + z[2, 2]) - (z[0, 0] + 2 * z[0, 1] + z[0, 2])) / (8 * dy_m)
    slope = math.degrees(math.atan(math.hypot(dzdx, dzdy)))
    if dzdx == 0 and dzdy == 0:
        return slope, -1.0  # flat: aspect undefined (ESRI convention -1)
    # dzdy is positive when terrain rises towards the south (row index increases
    # southwards). Compass aspect (degrees clockwise from north, direction of steepest
    # descent) follows the ESRI convention.
    raw = math.degrees(math.atan2(dzdy, -dzdx))
    if raw < 0:
        aspect = 90.0 - raw
    elif raw > 90.0:
        aspect = 360.0 - raw + 90.0
    else:
        aspect = 90.0 - raw
    return slope, aspect % 360.0


def sample_terrain(ds: rasterio.DatasetReader, lon: float, lat: float) -> TerrainSample:
    """Sample elevation and compute slope/aspect from the 3x3 neighbourhood."""
    x, y = _to_raster_crs(ds, lon, lat)
    try:
        row, col = ds.index(x, y)
    except Exception:
        return TerrainSample(None, None, None, "outside")
    if row < 0 or col < 0 or row >= ds.height or col >= ds.width:
        return TerrainSample(None, None, None, "outside")
    nodata = ds.nodata
    if row == 0 or col == 0 or row == ds.height - 1 or col == ds.width - 1:
        val = ds.read(1, window=Window(col, row, 1, 1))[0, 0]
        if nodata is not None and val == nodata or np.isnan(val):
            return TerrainSample(None, None, None, "nodata")
        return TerrainSample(float(val), None, None, "edge")
    win = ds.read(1, window=Window(col - 1, row - 1, 3, 3)).astype(float)
    if nodata is not None:
        win[win == nodata] = np.nan
    centre = win[1, 1]
    if np.isnan(centre):
        return TerrainSample(None, None, None, "nodata")
    if np.isnan(win).any():
        return TerrainSample(float(centre), None, None, "edge")
    res_x, res_y = ds.res
    if ds.crs is not None and CRS.from_user_input(ds.crs).is_geographic:
        m_lon, m_lat = metres_per_degree(lat)
        dx_m, dy_m = res_x * m_lon, res_y * m_lat
    else:
        dx_m, dy_m = float(res_x), float(res_y)
    slope, aspect = horn_slope_aspect(win, dx_m, dy_m)
    return TerrainSample(float(centre), slope, aspect, "ok")


def sample_categorical(ds: rasterio.DatasetReader, lon: float, lat: float) -> int | None:
    x, y = _to_raster_crs(ds, lon, lat)
    try:
        row, col = ds.index(x, y)
    except Exception:
        return None
    if row < 0 or col < 0 or row >= ds.height or col >= ds.width:
        return None
    val = ds.read(1, window=Window(col, row, 1, 1))[0, 0]
    if ds.nodata is not None and val == ds.nodata:
        return None
    return int(val)


def dem_tile_id(lon: float, lat: float) -> str:
    """USGS 3DEP 1x1 degree tile naming: the tile's north-west corner, e.g. n38w123 covers
    lat 37..38, lon -123..-122."""
    n = int(math.floor(lat)) + 1
    w = int(math.ceil(-lon))
    return f"n{n:02d}w{w:03d}"


def open_raster(path: str | Path) -> rasterio.DatasetReader:
    return rasterio.open(str(path))


def _to_raster_crs_many(
    ds: rasterio.DatasetReader, lons: np.ndarray, lats: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    lons = np.asarray(lons, dtype=float)
    lats = np.asarray(lats, dtype=float)
    if ds.crs is None or CRS.from_user_input(ds.crs).to_epsg() == 4326:
        return lons, lats
    t = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
    x, y = t.transform(lons, lats)
    return np.asarray(x), np.asarray(y)


def sample_values(
    ds: rasterio.DatasetReader, lons: np.ndarray, lats: np.ndarray, band: int = 1
) -> np.ndarray:
    """Vectorised point sampling; NoData and out-of-bounds become NaN."""
    xs, ys = _to_raster_crs_many(ds, lons, lats)
    out = np.full(len(xs), np.nan, dtype=float)
    if len(xs) == 0:
        return out
    rows, cols = rasterio.transform.rowcol(ds.transform, xs, ys)
    rows = np.asarray(rows)
    cols = np.asarray(cols)
    inside = (rows >= 0) & (cols >= 0) & (rows < ds.height) & (cols < ds.width)
    if not inside.any():
        return out
    # Read the bounding window of all inside points once (bounded by the raster extent).
    r0, r1 = int(rows[inside].min()), int(rows[inside].max())
    c0, c1 = int(cols[inside].min()), int(cols[inside].max())
    block = ds.read(band, window=Window(c0, r0, c1 - c0 + 1, r1 - r0 + 1)).astype(float)
    vals = block[rows[inside] - r0, cols[inside] - c0]
    if ds.nodata is not None:
        vals = np.where(vals == ds.nodata, np.nan, vals)
    out[inside] = vals
    return out


def window_fraction(
    ds: rasterio.DatasetReader,
    lons: np.ndarray,
    lats: np.ndarray,
    *,
    radius_m: float,
    predicate: Any,
    band: int = 1,
) -> np.ndarray:
    """Fraction of valid pixels within ``radius_m`` of each point satisfying ``predicate``
    (a function over an integer/float array returning a boolean array)."""
    xs, ys = _to_raster_crs_many(ds, lons, lats)
    out = np.full(len(xs), np.nan, dtype=float)
    if len(xs) == 0:
        return out
    res_x, res_y = ds.res
    if ds.crs is not None and CRS.from_user_input(ds.crs).is_geographic:
        m_lon, m_lat = metres_per_degree(float(np.nanmean(lats)) if len(lats) else 37.0)
        px, py = res_x * m_lon, res_y * m_lat
    else:
        px, py = float(res_x), float(res_y)
    rx, ry = max(1, int(round(radius_m / px))), max(1, int(round(radius_m / py)))
    rows, cols = rasterio.transform.rowcol(ds.transform, xs, ys)
    for i, (r, c) in enumerate(zip(np.asarray(rows), np.asarray(cols), strict=True)):
        r0, r1 = max(0, int(r) - ry), min(ds.height, int(r) + ry + 1)
        c0, c1 = max(0, int(c) - rx), min(ds.width, int(c) + rx + 1)
        if r1 <= r0 or c1 <= c0:
            continue
        win = ds.read(band, window=Window(c0, r0, c1 - c0, r1 - r0)).astype(float)
        valid = ~np.isnan(win)
        if ds.nodata is not None:
            valid &= win != ds.nodata
        n_valid = int(valid.sum())
        if n_valid == 0:
            continue
        out[i] = float(np.logical_and(predicate(win), valid).sum() / n_valid)
    return out

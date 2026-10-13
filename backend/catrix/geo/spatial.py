"""Spatial joins, distances and neighbourhood features (GeoPandas / Shapely STRtree).

Every function here accepts WGS84 inputs, reprojects to EPSG:3310 for metric work, and
returns plain numeric results so callers never operate on degrees by accident.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import STRtree
from shapely.geometry import Point

from catrix.config import GEOGRAPHIC_CRS, PROJECTED_CRS
from catrix.geo.crs import ensure_crs


def points_gdf(df: pd.DataFrame, lon_col: str = "lon", lat_col: str = "lat") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        df.copy(),
        geometry=gpd.points_from_xy(df[lon_col].astype(float), df[lat_col].astype(float)),
        crs=GEOGRAPHIC_CRS,
    )


def point_in_polygon_join(
    points: gpd.GeoDataFrame,
    polygons: gpd.GeoDataFrame,
    attrs: list[str],
    *,
    how: str = "left",
    priority: str | None = None,
    priority_order: list[str] | None = None,
) -> gpd.GeoDataFrame:
    """Left spatial join; when a point falls in several polygons keep the one with the
    highest priority (by ``priority_order``) or the first.

    Returns a frame indexed like ``points`` with the requested polygon attributes and a
    ``_n_matches`` column. Points with no match keep NaN attributes.
    """
    points = ensure_crs(points, GEOGRAPHIC_CRS)
    polygons = ensure_crs(polygons, GEOGRAPHIC_CRS)
    polygons = polygons[[*attrs, "geometry"]].copy()
    polygons = polygons[~polygons.geometry.isna() & ~polygons.geometry.is_empty]
    if "index_right" in polygons.columns:
        polygons = polygons.drop(columns=["index_right"])
    joined = gpd.sjoin(points[["geometry"]], polygons, how=how, predicate="within")
    joined["_n_matches"] = joined.groupby(level=0)["index_right"].transform(
        lambda s: s.notna().sum()
    )
    if priority and priority_order:
        rank = {v: i for i, v in enumerate(priority_order)}
        joined["_rank"] = joined[priority].map(rank).fillna(len(rank))
        joined = joined.sort_values("_rank").groupby(level=0).head(1).drop(columns="_rank")
    else:
        joined = joined.groupby(level=0).head(1)
    joined = joined.reindex(points.index)
    return joined


@dataclass
class NearestResult:
    index: np.ndarray  # index into the target frame (-1 when none within max_distance)
    distance_m: np.ndarray  # NaN when none


def nearest_distance_m(
    points: gpd.GeoDataFrame, targets: gpd.GeoDataFrame, max_distance_m: float | None = None
) -> NearestResult:
    """Distance from each point to its nearest target geometry in metres (EPSG:3310)."""
    if len(targets) == 0:
        n = len(points)
        return NearestResult(np.full(n, -1), np.full(n, np.nan))
    p = ensure_crs(points, PROJECTED_CRS)
    t = ensure_crs(targets, PROJECTED_CRS)
    tree = STRtree(t.geometry.values)
    idx = tree.nearest(p.geometry.values)
    dist = np.array(
        [pg.distance(tg) for pg, tg in zip(p.geometry.values, t.geometry.values[idx], strict=True)]
    )
    if max_distance_m is not None:
        too_far = dist > max_distance_m
        idx = np.where(too_far, -1, idx)
        dist = np.where(too_far, np.nan, dist)
    return NearestResult(np.asarray(idx), dist)


def count_within_radius(
    points: gpd.GeoDataFrame, targets: gpd.GeoDataFrame, radius_m: float
) -> np.ndarray:
    """Number of target geometries intersecting a buffer of ``radius_m`` around each point."""
    if len(targets) == 0:
        return np.zeros(len(points), dtype=int)
    p = ensure_crs(points, PROJECTED_CRS)
    t = ensure_crs(targets, PROJECTED_CRS)
    tree = STRtree(t.geometry.values)
    buffers = p.geometry.buffer(radius_m).values
    left, _right = tree.query(buffers, predicate="intersects")
    counts = np.bincount(left, minlength=len(points))
    return counts.astype(int)


def polygons_within_radius(
    points: gpd.GeoDataFrame, targets: gpd.GeoDataFrame, radius_m: float
) -> list[np.ndarray]:
    """For each point, the integer positions of targets intersecting its buffer."""
    p = ensure_crs(points, PROJECTED_CRS)
    t = ensure_crs(targets, PROJECTED_CRS)
    if len(t) == 0:
        return [np.array([], dtype=int) for _ in range(len(p))]
    tree = STRtree(t.geometry.values)
    buffers = p.geometry.buffer(radius_m).values
    left, right = tree.query(buffers, predicate="intersects")
    out: list[list[int]] = [[] for _ in range(len(p))]
    for lft, rgt in zip(left, right, strict=True):
        out[lft].append(int(rgt))
    return [np.array(o, dtype=int) for o in out]


def area_m2(gdf: gpd.GeoDataFrame) -> np.ndarray:
    return ensure_crs(gdf, PROJECTED_CRS).geometry.area.to_numpy()


def grid_cell_id(lon: float, lat: float, cell_km: float = 10.0) -> str:
    """Deterministic equal-area grid cell id in EPSG:3310 (for spatial blocking and
    portfolio concentration)."""
    from catrix.geo.crs import lonlat_to_projected

    x, y = lonlat_to_projected(np.array([lon]), np.array([lat]))
    size = cell_km * 1000.0
    return f"{int(np.floor(x[0] / size))}_{int(np.floor(y[0] / size))}"


def grid_cell_ids(lon: np.ndarray, lat: np.ndarray, cell_km: float = 10.0) -> np.ndarray:
    from catrix.geo.crs import lonlat_to_projected

    x, y = lonlat_to_projected(np.asarray(lon), np.asarray(lat))
    size = cell_km * 1000.0
    cx = np.floor(x / size).astype(int)
    cy = np.floor(y / size).astype(int)
    return np.array([f"{a}_{b}" for a, b in zip(cx, cy, strict=True)])


def grid_cell_centroid(cell_id: str, cell_km: float = 10.0) -> tuple[float, float]:
    from catrix.geo.crs import transformer

    cx, cy = (int(v) for v in cell_id.split("_"))
    size = cell_km * 1000.0
    x = (cx + 0.5) * size
    y = (cy + 0.5) * size
    lon, lat = transformer(PROJECTED_CRS, GEOGRAPHIC_CRS).transform(x, y)
    return float(lon), float(lat)


def point(lon: float, lat: float) -> Point:
    return Point(float(lon), float(lat))

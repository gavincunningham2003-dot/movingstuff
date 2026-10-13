"""Coordinate reference system helpers.

All metre-based measurements are done in EPSG:3310 (NAD83 / California Albers). Never
compute distances or areas from degrees.
"""

from __future__ import annotations

from functools import lru_cache

import geopandas as gpd
import numpy as np
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from catrix.config import GEOGRAPHIC_CRS, PROJECTED_CRS, WEB_MERCATOR


@lru_cache(maxsize=16)
def transformer(src: str, dst: str) -> Transformer:
    return Transformer.from_crs(CRS.from_user_input(src), CRS.from_user_input(dst), always_xy=True)


def to_projected(
    geom: BaseGeometry, src: str = GEOGRAPHIC_CRS, dst: str = PROJECTED_CRS
) -> BaseGeometry:
    t = transformer(src, dst)
    return transform(t.transform, geom)


def to_geographic(geom: BaseGeometry, src: str = PROJECTED_CRS) -> BaseGeometry:
    t = transformer(src, GEOGRAPHIC_CRS)
    return transform(t.transform, geom)


def lonlat_to_projected(
    lon: np.ndarray, lat: np.ndarray, dst: str = PROJECTED_CRS
) -> tuple[np.ndarray, np.ndarray]:
    t = transformer(GEOGRAPHIC_CRS, dst)
    x, y = t.transform(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
    return np.asarray(x), np.asarray(y)


def web_mercator_to_lonlat(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t = transformer(WEB_MERCATOR, GEOGRAPHIC_CRS)
    lon, lat = t.transform(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
    return np.asarray(lon), np.asarray(lat)


def ensure_crs(gdf: gpd.GeoDataFrame, target: str = GEOGRAPHIC_CRS) -> gpd.GeoDataFrame:
    """Set or reproject a GeoDataFrame to ``target``. Raises if the CRS is unknown and the
    coordinates do not look geographic — we never guess silently."""
    if gdf.crs is None:
        bounds = gdf.total_bounds
        if np.all(np.abs(bounds[[0, 2]]) <= 180) and np.all(np.abs(bounds[[1, 3]]) <= 90):
            gdf = gdf.set_crs(GEOGRAPHIC_CRS)
        else:
            raise ValueError("GeoDataFrame has no CRS and coordinates are not geographic")
    if not CRS.from_user_input(gdf.crs).equals(CRS.from_user_input(target)):
        gdf = gdf.to_crs(target)
    return gdf


def metres_per_degree(lat: float) -> tuple[float, float]:
    """Approximate metres per degree of (longitude, latitude) at a latitude. Used only to
    convert raster pixel spacing to metres for slope calculation."""
    lat_rad = np.radians(lat)
    m_per_deg_lat = 111_132.954 - 559.822 * np.cos(2 * lat_rad) + 1.175 * np.cos(4 * lat_rad)
    m_per_deg_lon = 111_412.84 * np.cos(lat_rad) - 93.5 * np.cos(3 * lat_rad)
    return float(m_per_deg_lon), float(m_per_deg_lat)

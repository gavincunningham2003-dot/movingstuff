"""Coordinate and geometry validation."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from catrix.config import CALIFORNIA_BBOX


@dataclass
class CoordinateCheck:
    n_total: int
    n_missing: int
    n_impossible: int
    n_outside_california: int
    n_zero: int
    valid_mask: np.ndarray = field(repr=False)

    @property
    def n_valid(self) -> int:
        return int(self.valid_mask.sum())


def is_valid_lat(lat: float) -> bool:
    return isinstance(lat, int | float) and not math.isnan(lat) and -90.0 <= lat <= 90.0


def is_valid_lon(lon: float) -> bool:
    return isinstance(lon, int | float) and not math.isnan(lon) and -180.0 <= lon <= 180.0


def in_california_bbox(lon: float, lat: float) -> bool:
    minx, miny, maxx, maxy = CALIFORNIA_BBOX
    return minx <= lon <= maxx and miny <= lat <= maxy


def check_coordinates(
    df: pd.DataFrame, lat_col: str = "lat", lon_col: str = "lon", *, require_california: bool = True
) -> CoordinateCheck:
    """Vectorised validation. Zero/zero is flagged separately because it is a classic
    geocoder failure value that would otherwise pass range checks."""
    lat = pd.to_numeric(df[lat_col], errors="coerce")
    lon = pd.to_numeric(df[lon_col], errors="coerce")
    missing = lat.isna() | lon.isna()
    impossible = (~missing) & ((lat.abs() > 90) | (lon.abs() > 180))
    zero = (~missing) & (lat == 0) & (lon == 0)
    minx, miny, maxx, maxy = CALIFORNIA_BBOX
    inside = (lon >= minx) & (lon <= maxx) & (lat >= miny) & (lat <= maxy)
    # Categories are mutually exclusive: a 0,0 point is reported as 'zero', not 'outside'.
    outside = (~missing) & (~impossible) & (~zero) & (~inside)
    valid = ~(missing | impossible | zero)
    if require_california:
        valid &= ~outside
    return CoordinateCheck(
        n_total=len(df),
        n_missing=int(missing.sum()),
        n_impossible=int(impossible.sum()),
        n_outside_california=int(outside.sum()),
        n_zero=int(zero.sum()),
        valid_mask=valid.to_numpy(),
    )


def dedup_key(lat: float, lon: float, precision_m: float = 1.0) -> str:
    """Deterministic key for near-duplicate locations.

    Rounds to roughly ``precision_m`` metres in latitude (1e-5 deg ~ 1.1 m) so that
    identical points from different sources collapse together. Longitude step is scaled by
    cos(lat) so the cell is approximately square.
    """
    lat_step = precision_m / 111_320.0
    lon_step = lat_step / max(math.cos(math.radians(lat)), 1e-6)
    return f"{round(lat / lat_step):d}:{round(lon / lon_step):d}"

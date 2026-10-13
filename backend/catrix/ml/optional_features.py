"""Year-matched optional features (climate, drought, fuels, land cover) for the
location-year table and for ad-hoc prediction points.

All four tables are produced by connectors as Parquet under ``data/processed/ml``:

* ``climate_features``   (address_id, feature_year, precip_*, tmax_*)         — PRISM
* ``drought_features``   (county_fips, feature_year, drought_*)                — USDM
* ``fuel_features``      (address_id, lf_version, data_year, fbfm40, ...)     — LANDFIRE
* ``landcover_features`` (address_id, nlcd_year, lc_*)                         — NLCD

Fuel and land-cover products are periodic, so each feature year T is matched to the
latest version/epoch with data year ≤ T. Years earlier than the first available version
use that version and record the lag (``*_version_lag_years``) so the model (and the
reader) can see the anachronism instead of it being hidden.
"""

from __future__ import annotations

import bisect
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from catrix.ingest.store import read_parquet
from catrix.logging import get_logger

log = get_logger(__name__)

CLIMATE_COLS = [
    "precip_pct_normal",
    "precip_prev_year_pct_normal",
    "precip_3y_pct_normal",
    "tmax_anomaly_c",
]
DROUGHT_COLS = ["drought_d2_share", "drought_d3_max"]
FUEL_COLS = ["fbfm40", "fuel_group", "fuel_burnable", "canopy_cover_pct", "burnable_frac_500m"]
LC_COLS = ["lc_developed_intensity", "lc_veg_frac_500m", "lc_developed_frac_500m"]


def match_version_year(target_year: int, available: list[int]) -> tuple[int, int]:
    """Latest available data year ≤ target, else the earliest. Returns (year, lag)."""
    if not available:
        raise ValueError("no versions available")
    years = sorted(available)
    i = bisect.bisect_right(years, target_year) - 1
    if i < 0:
        return years[0], years[0] - target_year  # positive lag = version is newer than T
    return years[i], target_year - years[i]  # 0 when matched exactly


def _expand_periodic(
    table: pd.DataFrame, year_col: str, cols: list[str], feature_years: range, lag_name: str
) -> pd.DataFrame:
    avail = sorted(int(y) for y in table[year_col].dropna().unique())
    by_year = {y: g.set_index("address_id")[cols] for y, g in table.groupby(year_col)}
    parts = []
    for T in feature_years:
        y, lag = match_version_year(T, avail)
        g = by_year[y].copy()
        g["feature_year"] = T
        g[lag_name] = abs(lag)
        parts.append(g.reset_index())
    return pd.concat(parts, ignore_index=True)


def load_optional_features(
    addresses: pd.DataFrame, feature_years: tuple[int, int]
) -> pd.DataFrame | None:
    """Assemble one frame keyed (address_id, feature_year) from whichever tables exist.
    ``addresses`` needs ``address_id`` and ``county_fips``."""
    years = range(feature_years[0], feature_years[1] + 1)
    base = pd.MultiIndex.from_product(
        [addresses["address_id"].unique(), list(years)], names=["address_id", "feature_year"]
    ).to_frame(index=False)
    out = base.merge(
        addresses[["address_id", "county_fips"]].drop_duplicates(), on="address_id", how="left"
    )
    found: list[str] = []

    climate = read_parquet("climate_features", subdir="ml")
    if climate is not None and len(climate):
        out = out.merge(
            climate[["address_id", "feature_year", *CLIMATE_COLS]],
            on=["address_id", "feature_year"],
            how="left",
        )
        found.append("climate")

    drought = read_parquet("drought_features", subdir="ml")
    if drought is not None and len(drought):
        out = out.merge(
            drought[["county_fips", "feature_year", *DROUGHT_COLS]],
            on=["county_fips", "feature_year"],
            how="left",
        )
        found.append("drought")

    fuel = read_parquet("fuel_features", subdir="ml")
    if fuel is not None and len(fuel):
        exp = _expand_periodic(fuel, "data_year", FUEL_COLS, years, "fuel_version_lag_years")
        out = out.merge(exp, on=["address_id", "feature_year"], how="left")
        found.append("fuel")

    lc = read_parquet("landcover_features", subdir="ml")
    if lc is not None and len(lc):
        exp = _expand_periodic(lc, "nlcd_year", LC_COLS, years, "lc_version_lag_years")
        out = out.merge(exp, on=["address_id", "feature_year"], how="left")
        found.append("landcover")

    if not found:
        log.info("optional_features_absent")
        return None
    log.info("optional_features_loaded", tables=found, rows=len(out))
    return out.drop(columns=["county_fips"])


# --------------------------------------------------------------------------- prediction time
def _latest_year_rows(
    table: pd.DataFrame, year_col: str, feature_year: int
) -> tuple[pd.DataFrame, int]:
    avail = sorted(int(y) for y in table[year_col].dropna().unique())
    y, lag = match_version_year(feature_year, avail)
    return table[table[year_col] == y], abs(lag)


def optional_features_for_points(
    session: Session, points: pd.DataFrame, feature_year: int
) -> pd.DataFrame:
    """Optional features for arbitrary points at prediction time.

    * Points whose index is an ingested ``address_id`` reuse the per-address tables.
    * Other points (portfolio uploads) are sampled from the cached rasters when present
      (PRISM latest grids, latest LANDFIRE version, cached NLCD tiles) and from the county
      drought table; anything unavailable stays NaN, which the models' imputers handle and
      which is reported honestly as a feature value of null.
    """
    out = pd.DataFrame(index=points.index)
    for c in (
        CLIMATE_COLS
        + DROUGHT_COLS
        + FUEL_COLS
        + LC_COLS
        + ["fuel_version_lag_years", "lc_version_lag_years"]
    ):
        out[c] = np.nan

    climate = read_parquet("climate_features", subdir="ml")
    drought = read_parquet("drought_features", subdir="ml")
    fuel = read_parquet("fuel_features", subdir="ml")
    lc = read_parquet("landcover_features", subdir="ml")

    idx_as_addr = pd.Index(points.index)
    if climate is not None and len(climate):
        rows, _ = _latest_year_rows(climate, "feature_year", feature_year)
        rows = rows.set_index("address_id")
        hit = idx_as_addr.intersection(rows.index)
        if len(hit):
            out.loc[hit, CLIMATE_COLS] = rows.loc[hit, CLIMATE_COLS].values
    if drought is not None and len(drought) and "county_fips" in points.columns:
        rows, _ = _latest_year_rows(drought, "feature_year", feature_year)
        m = rows.set_index("county_fips")[DROUGHT_COLS]
        fips = points["county_fips"]
        ok = fips.isin(m.index)
        if ok.any():
            out.loc[ok, DROUGHT_COLS] = m.loc[fips[ok]].values
    if fuel is not None and len(fuel):
        rows, lag = _latest_year_rows(fuel, "data_year", feature_year)
        rows = rows.set_index("address_id")
        hit = idx_as_addr.intersection(rows.index)
        if len(hit):
            out.loc[hit, FUEL_COLS] = rows.loc[hit, FUEL_COLS].values
            out.loc[hit, "fuel_version_lag_years"] = lag
    if lc is not None and len(lc):
        rows, lag = _latest_year_rows(lc, "nlcd_year", feature_year)
        rows = rows.set_index("address_id")
        hit = idx_as_addr.intersection(rows.index)
        if len(hit):
            out.loc[hit, LC_COLS] = rows.loc[hit, LC_COLS].values
            out.loc[hit, "lc_version_lag_years"] = lag

    # Raster fallback for points that are not ingested addresses.
    missing = out["fuel_group"].isna()
    if missing.any():
        _raster_fallback(points[missing], out, feature_year)
    return out


def _raster_fallback(points: pd.DataFrame, out: pd.DataFrame, feature_year: int) -> None:
    try:
        import rasterio

        from catrix.geo.raster import sample_values, window_fraction
        from catrix.ingest.landfire import (
            VERSION_DATA_YEAR,
            available_versions,
            find_layer,
            fuel_group,
            is_burnable,
        )
    except Exception as exc:
        log.warning("raster_fallback_unavailable", error=repr(exc))
        return
    lons = points["lon"].to_numpy(float)
    lats = points["lat"].to_numpy(float)
    versions = available_versions()
    if versions:
        years = {VERSION_DATA_YEAR.get(v, int(v[2:])): v for v in versions}
        y, lag = match_version_year(feature_year, sorted(years))
        vdir = versions[years[y]]
        fb = find_layer(vdir, "FBFM40")
        if fb is not None:
            with rasterio.open(fb[0]) as ds:
                codes = sample_values(ds, lons, lats, band=fb[1])
                burn = window_fraction(
                    ds,
                    lons,
                    lats,
                    band=fb[1],
                    radius_m=500.0,
                    predicate=lambda a: (a >= 101) & (a <= 204),
                )
            ci = [None if np.isnan(c) else int(c) for c in codes]
            out.loc[points.index, "fbfm40"] = codes
            out.loc[points.index, "fuel_group"] = [
                np.nan if fuel_group(c) is None else fuel_group(c) for c in ci
            ]
            out.loc[points.index, "fuel_burnable"] = [
                np.nan if is_burnable(c) is None else is_burnable(c) for c in ci
            ]
            out.loc[points.index, "burnable_frac_500m"] = burn
            out.loc[points.index, "fuel_version_lag_years"] = abs(lag)
            cc = find_layer(vdir, "CC")
            if cc is not None and cc != fb:
                with rasterio.open(cc[0]) as ds:
                    out.loc[points.index, "canopy_cover_pct"] = sample_values(
                        ds, lons, lats, band=cc[1]
                    )


def describe_optional(df: pd.DataFrame) -> dict[str, Any]:
    """Coverage summary used in metrics.json so readers know which optional features existed."""
    cols = [c for c in CLIMATE_COLS + DROUGHT_COLS + FUEL_COLS + LC_COLS if c in df.columns]
    return {c: float(df[c].notna().mean()) for c in cols}

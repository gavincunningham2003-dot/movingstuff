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


def match_version_year(target_year: int, available: list[int]) -> tuple[int, int] | None:
    """Latest available data year <= target. Returns (year, lag) or None when every
    available edition post-dates ``target_year``.

    Never backfill from a later edition: a vegetation or fuel map collected after year T
    reflects fires that happened between T and the collection date, i.e. it can encode
    the label. Rows for such years keep NaN features (the models handle missing values)
    and the lag is therefore always >= 0.
    """
    if not available:
        raise ValueError("no versions available")
    years = sorted(available)
    i = bisect.bisect_right(years, target_year) - 1
    if i < 0:
        return None
    return years[i], target_year - years[i]  # 0 when matched exactly


def _expand_periodic(
    table: pd.DataFrame, year_col: str, cols: list[str], feature_years: range, lag_name: str
) -> pd.DataFrame:
    avail = sorted(int(y) for y in table[year_col].dropna().unique())
    by_year = {y: g.set_index("address_id")[cols] for y, g in table.groupby(year_col)}
    parts = []
    for T in feature_years:
        matched = match_version_year(T, avail)
        if matched is None:
            continue  # no edition from <= T exists: leave the year's features missing
        y, lag = matched
        g = by_year[y].copy()
        g["feature_year"] = T
        g[lag_name] = lag
        parts.append(g.reset_index())
    if not parts:
        return pd.DataFrame(columns=["address_id", "feature_year", *cols, lag_name])
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
) -> tuple[pd.DataFrame, int | None]:
    avail = sorted(int(y) for y in table[year_col].dropna().unique())
    matched = match_version_year(feature_year, avail)
    if matched is None:
        return table.iloc[0:0], None
    y, lag = matched
    return table[table[year_col] == y], lag


def optional_features_for_points(
    session: Session, points: pd.DataFrame, feature_year: int
) -> pd.DataFrame:
    """Optional features for arbitrary points at prediction time.

    * Rows that carry an explicit ``address_id`` column naming an ingested address reuse
      the per-address tables built by the connectors.
    * Every other row (portfolio uploads, map clicks) is sampled **from its own
      coordinates**: PRISM annual grids and 1991-2020 normals, the latest LANDFIRE
      edition <= T, the NLCD epoch <= T (tiles fetched on demand), and the county drought
      table. Anything unavailable stays NaN and is reported as such.

    The DataFrame index is never interpreted as an address id.
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

    ids = (
        pd.to_numeric(points["address_id"], errors="coerce")
        if "address_id" in points.columns
        else pd.Series(np.nan, index=points.index)
    )
    has_id = ids.notna().to_numpy()

    def _fill(table: pd.DataFrame | None, year_col: str, cols: list[str], lag_col: str | None):
        if table is None or not len(table) or not has_id.any():
            return
        rows, lag = _latest_year_rows(table, year_col, feature_year)
        if not len(rows):
            return
        rows = rows.drop_duplicates("address_id").set_index("address_id")
        wanted = ids[has_id].astype(int)
        hit_mask = wanted.isin(rows.index).to_numpy()
        if not hit_mask.any():
            return
        hit_idx = wanted.index[hit_mask]
        out.loc[hit_idx, cols] = rows.loc[wanted[hit_mask].to_numpy(), cols].to_numpy()
        if lag_col is not None and lag is not None:
            out.loc[hit_idx, lag_col] = lag

    _fill(read_parquet("climate_features", subdir="ml"), "feature_year", CLIMATE_COLS, None)
    _fill(
        read_parquet("fuel_features", subdir="ml"), "data_year", FUEL_COLS, "fuel_version_lag_years"
    )
    _fill(
        read_parquet("landcover_features", subdir="ml"),
        "nlcd_year",
        LC_COLS,
        "lc_version_lag_years",
    )

    drought = read_parquet("drought_features", subdir="ml")
    if drought is not None and len(drought) and "county_fips" in points.columns:
        rows, _ = _latest_year_rows(drought, "feature_year", feature_year)
        if len(rows):
            m = rows.drop_duplicates("county_fips").set_index("county_fips")[DROUGHT_COLS]
            fips = points["county_fips"]
            ok = fips.isin(m.index)
            if ok.any():
                out.loc[ok, DROUGHT_COLS] = m.loc[fips[ok]].to_numpy()

    # Coordinate-based sampling for rows that are not ingested addresses (or whose
    # per-address rows are missing).
    need_fuel = out["fuel_group"].isna()
    if need_fuel.any():
        _raster_fallback(points[need_fuel], out, feature_year)
    need_lc = out["lc_veg_frac_500m"].isna()
    if need_lc.any():
        _landcover_fallback(points[need_lc], out, feature_year)
    need_climate = out["precip_pct_normal"].isna()
    if need_climate.any():
        _climate_fallback(points[need_climate], out, feature_year)
    return out


def _raster_fallback(points: pd.DataFrame, out: pd.DataFrame, feature_year: int) -> None:
    """LANDFIRE fuel features from the latest local edition with data year <= T."""
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
    if not versions:
        return
    years = {VERSION_DATA_YEAR.get(v, int(v[2:])): v for v in versions}
    matched = match_version_year(feature_year, sorted(years))
    if matched is None:
        return  # every local edition post-dates T: leave NaN rather than leak
    y, lag = matched
    vdir = versions[years[y]]
    fb = find_layer(vdir, "FBFM40")
    if fb is None:
        return
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
    out.loc[points.index, "fuel_version_lag_years"] = lag
    cc = find_layer(vdir, "CC")
    if cc is not None and cc != fb:
        with rasterio.open(cc[0]) as ds:
            out.loc[points.index, "canopy_cover_pct"] = sample_values(ds, lons, lats, band=cc[1])


def _landcover_fallback(points: pd.DataFrame, out: pd.DataFrame, feature_year: int) -> None:
    """NLCD land-cover features from the epoch <= T, sampled per 0.5-degree WCS tile
    (cached tiles are reused; missing tiles are fetched)."""
    try:
        from catrix.ingest.nlcd import EPOCHS, features_from_raster, fetch_tile, tile_id
    except Exception as exc:
        log.warning("landcover_fallback_unavailable", error=repr(exc))
        return
    matched = match_version_year(feature_year, list(EPOCHS))
    if matched is None:
        return
    year, lag = matched
    by_tile: dict[str, list[int]] = {}
    for pos, (lon, lat) in enumerate(
        zip(points["lon"].to_numpy(float), points["lat"].to_numpy(float), strict=True)
    ):
        by_tile.setdefault(tile_id(lon, lat), []).append(pos)
    for tid, positions in by_tile.items():
        try:
            path = fetch_tile(year, tid)
        except Exception as exc:
            log.warning("landcover_tile_unavailable", tile=tid, year=year, error=repr(exc))
            continue
        sub = points.iloc[positions]
        feats = features_from_raster(
            path,
            np.arange(len(sub)),
            sub["lon"].to_numpy(float),
            sub["lat"].to_numpy(float),
            year,
        )
        out.loc[sub.index, LC_COLS] = feats[LC_COLS].to_numpy()
        out.loc[sub.index, "lc_version_lag_years"] = lag


def _climate_fallback(points: pd.DataFrame, out: pd.DataFrame, feature_year: int) -> None:
    """PRISM climate features from the annual grids for T, T-1, T-2 and the 1991-2020
    normals, sampled at each point's coordinates."""
    try:
        from catrix.ingest.prism import NORMAL_YEARS, fetch_annual_grid, sample_grid
    except Exception as exc:
        log.warning("climate_fallback_unavailable", error=repr(exc))
        return
    lons = points["lon"].to_numpy(float)
    lats = points["lat"].to_numpy(float)

    def grid(element: str, year: int) -> np.ndarray | None:
        try:
            return sample_grid(fetch_annual_grid(element, year), lons, lats)
        except Exception as exc:
            log.warning("climate_grid_unavailable", element=element, year=year, error=repr(exc))
            return None

    n0, n1 = NORMAL_YEARS
    ppt_norm_parts = [g for y in range(n0, n1 + 1) if (g := grid("ppt", y)) is not None]
    tmax_norm_parts = [g for y in range(n0, n1 + 1) if (g := grid("tmax", y)) is not None]
    if len(ppt_norm_parts) < 20 or len(tmax_norm_parts) < 20:
        return
    ppt_norm = np.nanmean(np.vstack(ppt_norm_parts), axis=0)
    tmax_norm = np.nanmean(np.vstack(tmax_norm_parts), axis=0)
    pct: dict[int, np.ndarray] = {}
    for y in (feature_year, feature_year - 1, feature_year - 2):
        g = grid("ppt", y)
        if g is not None:
            pct[y] = 100.0 * g / ppt_norm
    if feature_year not in pct:
        return
    out.loc[points.index, "precip_pct_normal"] = pct[feature_year]
    if feature_year - 1 in pct:
        out.loc[points.index, "precip_prev_year_pct_normal"] = pct[feature_year - 1]
    three = [pct[y] for y in (feature_year, feature_year - 1, feature_year - 2) if y in pct]
    if len(three) >= 2:
        out.loc[points.index, "precip_3y_pct_normal"] = np.nanmean(np.vstack(three), axis=0)
    tm = grid("tmax", feature_year)
    if tm is not None:
        out.loc[points.index, "tmax_anomaly_c"] = tm - tmax_norm


def describe_optional(df: pd.DataFrame) -> dict[str, Any]:
    """Coverage summary used in metrics.json so readers know which optional features existed."""
    cols = [c for c in CLIMATE_COLS + DROUGHT_COLS + FUEL_COLS + LC_COLS if c in df.columns]
    return {c: float(df[c].notna().mean()) for c in cols}

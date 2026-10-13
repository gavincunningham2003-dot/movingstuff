"""Location-year modelling dataset for the wildfire perimeter-exposure model.

Target (documented in ML_METHODOLOGY.md):
    y[address, T+1] = 1 if the address point lies inside at least one CAL FIRE perimeter
    whose YEAR_ == T+1, else 0.
    This measures *mapped fire-perimeter exposure*, not building damage. Absence of a
    perimeter is "no mapped perimeter", so label_quality records mapping-era caveats.

Feature set for year T uses only information knowable by 31 December of year T:
    * fire history (perimeters with YEAR_ <= T): counts within 1/5/10 km over the previous
      10 and 30 years, years since the last perimeter within 1 km, ever-inside flag, acres
      burned within 5 km in the previous 5 years, distance to the nearest perimeter;
    * static terrain: elevation, slope, northness/eastness (cos/sin of aspect);
    * static context: building density within 100 m (Microsoft footprints, imagery
      2014-2020; flagged as a weak anachronism for early years in the docs) and
      distance to the nearest Quaternary fault is *not* used (irrelevant to fire);
    * optional year-matched climate/drought features when their connectors have run
      (NOAA GSOM anomalies, USDM D2+ share) — columns are present only if available.

Present-day FHSZ classification is deliberately excluded (it post-dates the targets).

Pipeline:
    1. One PostGIS pass collects, per address, every perimeter within 10 km (year,
       distance, inside flag, acres) → Parquet (``ml/perimeter_neighbours``).
    2. DuckDB aggregates that table per (address, year T) into features and labels.
    3. Spatial blocks (50 km EPSG:3310 grid) are attached for block validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from catrix.config import get_settings
from catrix.db.models import Address, AddressBuilding, EnvironmentalObservation
from catrix.geo.spatial import grid_cell_ids
from catrix.ingest.store import chunked, write_parquet
from catrix.logging import get_logger

log = get_logger(__name__)

NEIGHBOUR_RADIUS_M = 10_000
MIN_LABEL_YEAR = 1990  # FRAP mapping is materially more complete from the 1990s
DATASET_VERSION = "wf-ly-1.0"
SPATIAL_BLOCK_KM = 50.0

STATIC_FEATURES = ["elevation_m", "slope_deg", "northness", "eastness", "buildings_within_100m"]
HISTORY_FEATURES = [
    "n_perim_1km_10y",
    "n_perim_5km_10y",
    "n_perim_10km_10y",
    "n_perim_5km_30y",
    "years_since_perim_1km",
    "ever_inside_perimeter",
    "acres_5km_5y",
    "dist_nearest_perim_m",
]
OPTIONAL_FEATURES = [
    # climate (PRISM, year T observations)
    "precip_pct_normal",
    "precip_prev_year_pct_normal",
    "precip_3y_pct_normal",
    "tmax_anomaly_c",
    # drought (USDM county, year T)
    "drought_d2_share",
    "drought_d3_max",
    # fuels (LANDFIRE version with data year <= T)
    "fbfm40",
    "fuel_group",
    "fuel_burnable",
    "canopy_cover_pct",
    "burnable_frac_500m",
    "fuel_version_lag_years",
    # land cover (NLCD epoch <= T)
    "lc_developed_intensity",
    "lc_veg_frac_500m",
    "lc_developed_frac_500m",
    "lc_version_lag_years",
]


@dataclass
class DatasetSpec:
    feature_years: tuple[int, int]  # inclusive range of T
    train_years: tuple[int, int]
    val_years: tuple[int, int]
    test_years: tuple[int, int]
    spatial_holdout_fraction: float = 0.15
    version: str = DATASET_VERSION


DEFAULT_SPEC = DatasetSpec(
    feature_years=(1995, 2023),
    train_years=(1995, 2014),
    val_years=(2015, 2018),
    test_years=(2019, 2023),
)


def collect_perimeter_neighbours(session: Session, batch: int = 500) -> pd.DataFrame:
    rows = session.execute(select(Address.id, Address.lon, Address.lat).order_by(Address.id)).all()
    out: list[dict] = []
    for chunk in chunked(rows, batch):
        values = ", ".join(f"({i}, {lon!r}, {lat!r})" for i, lon, lat in chunk)
        sql = text(
            f"""
            WITH pts(i, lon, lat) AS (VALUES {values}),
            p AS (SELECT i, ST_SetSRID(ST_MakePoint(lon, lat), 4326) AS g FROM pts)
            SELECT p.i AS address_id, w.year, w.gis_acres,
                   ST_Distance(w.geom::geography, p.g::geography) AS distance_m,
                   ST_Intersects(w.geom, p.g) AS inside
            FROM p JOIN wildfire_events w
              ON w.year IS NOT NULL AND w.year >= :miny AND ST_DWithin(w.geom::geography, p.g::geography, :r)
            """
        )
        for r in session.execute(
            sql, {"miny": MIN_LABEL_YEAR - 30, "r": NEIGHBOUR_RADIUS_M}
        ).mappings():
            out.append(dict(r))
        log.info("perimeter_neighbours_batch", n=len(out))
    df = pd.DataFrame(out, columns=["address_id", "year", "gis_acres", "distance_m", "inside"])
    df["inside"] = df["inside"].astype(bool)
    write_parquet(df, "perimeter_neighbours", subdir="ml")
    return df


def static_features(session: Session) -> pd.DataFrame:
    q = (
        select(
            Address.id.label("address_id"),
            Address.lon,
            Address.lat,
            Address.county_fips,
            EnvironmentalObservation.elevation_m,
            EnvironmentalObservation.slope_deg,
            EnvironmentalObservation.aspect_deg,
            AddressBuilding.buildings_within_100m,
        )
        .outerjoin(EnvironmentalObservation, EnvironmentalObservation.address_id == Address.id)
        .outerjoin(AddressBuilding, AddressBuilding.address_id == Address.id)
    )
    df = pd.DataFrame(
        session.execute(q).all(),
        columns=[
            "address_id",
            "lon",
            "lat",
            "county_fips",
            "elevation_m",
            "slope_deg",
            "aspect_deg",
            "buildings_within_100m",
        ],
    )
    asp = np.radians(df["aspect_deg"].where(df["aspect_deg"] >= 0))
    df["northness"] = np.cos(asp)
    df["eastness"] = np.sin(asp)
    df["spatial_block"] = grid_cell_ids(
        df["lon"].to_numpy(float), df["lat"].to_numpy(float), SPATIAL_BLOCK_KM
    )
    return df


def build_location_year_table(
    neighbours: pd.DataFrame,
    static: pd.DataFrame,
    spec: DatasetSpec,
    optional: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """DuckDB aggregation of neighbour records into features for every (address, T)."""
    y0, y1 = spec.feature_years
    con = duckdb.connect()
    con.register("nb", neighbours)
    con.register("st", static)
    years = pd.DataFrame({"T": list(range(y0, y1 + 1))})
    con.register("yrs", years)
    sql = """
    WITH grid AS (SELECT st.address_id, yrs.T FROM st CROSS JOIN yrs),
    feats AS (
      SELECT g.address_id, g.T,
        count(*) FILTER (WHERE nb.year <= g.T AND nb.year > g.T-10 AND nb.distance_m <= 1000)  AS n_perim_1km_10y,
        count(*) FILTER (WHERE nb.year <= g.T AND nb.year > g.T-10 AND nb.distance_m <= 5000)  AS n_perim_5km_10y,
        count(*) FILTER (WHERE nb.year <= g.T AND nb.year > g.T-10 AND nb.distance_m <= 10000) AS n_perim_10km_10y,
        count(*) FILTER (WHERE nb.year <= g.T AND nb.year > g.T-30 AND nb.distance_m <= 5000)  AS n_perim_5km_30y,
        g.T - max(nb.year) FILTER (WHERE nb.year <= g.T AND nb.distance_m <= 1000)                AS years_since_perim_1km,
        CAST(bool_or(nb.inside AND nb.year <= g.T) AS INTEGER)                                     AS ever_inside_perimeter,
        coalesce(sum(nb.gis_acres) FILTER (WHERE nb.year <= g.T AND nb.year > g.T-5 AND nb.distance_m <= 5000), 0) AS acres_5km_5y,
        min(nb.distance_m) FILTER (WHERE nb.year <= g.T)                                           AS dist_nearest_perim_m,
        CAST(bool_or(nb.inside AND nb.year = g.T + 1) AS INTEGER)                                  AS label
      FROM grid g LEFT JOIN nb ON nb.address_id = g.address_id
      GROUP BY g.address_id, g.T
    )
    SELECT f.*, st.lon, st.lat, st.county_fips, st.elevation_m, st.slope_deg, st.northness, st.eastness,
           st.buildings_within_100m, st.spatial_block
    FROM feats f JOIN st USING (address_id)
    ORDER BY f.address_id, f.T
    """
    df = con.execute(sql).df()
    con.close()
    df = df.rename(columns={"T": "feature_year"})
    df["target_year"] = df["feature_year"] + 1
    df["label"] = df["label"].fillna(0).astype(int)
    df["ever_inside_perimeter"] = df["ever_inside_perimeter"].fillna(0).astype(int)
    df["years_since_perim_1km"] = df["years_since_perim_1km"].fillna(60).clip(upper=60)
    df["dist_nearest_perim_m"] = df["dist_nearest_perim_m"].fillna(NEIGHBOUR_RADIUS_M)
    df["label_quality"] = np.where(df["target_year"] >= 2000, "mapped_modern", "mapped_legacy")
    if optional is not None and len(optional):
        df = df.merge(optional, on=["address_id", "feature_year"], how="left")
    df["dataset_version"] = spec.version
    return df


def assign_splits(df: pd.DataFrame, spec: DatasetSpec, seed: int | None = None) -> pd.DataFrame:
    """Chronological split by feature year plus a held-out set of spatial blocks that is
    removed from train/val entirely (geographic generalisation test)."""
    rng = np.random.default_rng(seed if seed is not None else get_settings().random_seed)
    blocks = np.array(sorted(df["spatial_block"].unique()))
    n_hold = max(1, int(round(len(blocks) * spec.spatial_holdout_fraction)))
    holdout_blocks = set(rng.choice(blocks, size=n_hold, replace=False).tolist())
    fy = df["feature_year"]
    split = np.full(len(df), "unused", dtype=object)
    split[(fy >= spec.train_years[0]) & (fy <= spec.train_years[1])] = "train"
    split[(fy >= spec.val_years[0]) & (fy <= spec.val_years[1])] = "val"
    split[(fy >= spec.test_years[0]) & (fy <= spec.test_years[1])] = "test"
    in_hold = df["spatial_block"].isin(holdout_blocks).to_numpy()
    split = np.where(in_hold & (split != "unused"), "spatial_test", split)
    df = df.copy()
    df["split"] = split
    df.attrs["holdout_blocks"] = sorted(holdout_blocks)
    return df


def build_dataset(
    session: Session, spec: DatasetSpec = DEFAULT_SPEC, *, optional: pd.DataFrame | None = None
) -> pd.DataFrame:
    nb = collect_perimeter_neighbours(session)
    st = static_features(session)
    if optional is None:
        from catrix.ml.optional_features import load_optional_features

        optional = load_optional_features(st[["address_id", "county_fips"]], spec.feature_years)
    df = build_location_year_table(nb, st, spec, optional)
    df = assign_splits(df, spec)
    path = write_parquet(df, f"dataset_{spec.version}", subdir="ml")
    log.info(
        "ml_dataset_built",
        rows=len(df),
        positives=int(df["label"].sum()),
        path=str(path),
        built_at=datetime.now(UTC).isoformat(),
    )
    return df


def load_dataset(version: str = DATASET_VERSION) -> pd.DataFrame | None:
    p: Path = get_settings().processed_dir / "ml" / f"dataset_{version}.parquet"
    return pd.read_parquet(p) if p.exists() else None


def feature_columns(df: pd.DataFrame) -> list[str]:
    cols = [c for c in HISTORY_FEATURES + STATIC_FEATURES if c in df.columns]
    cols += [c for c in OPTIONAL_FEATURES if c in df.columns and df[c].notna().mean() > 0.5]
    return cols


def leakage_checks(df: pd.DataFrame) -> list[str]:
    """Assertions that no target-year information entered the features."""
    problems: list[str] = []
    if (df["target_year"] != df["feature_year"] + 1).any():
        problems.append("target_year must equal feature_year + 1")
    # ever_inside_perimeter at T must not be 1 solely because of a T+1 perimeter: a row
    # with label 1 and ever_inside 1 is fine only if an earlier perimeter exists; we can't
    # verify per row here, but the DuckDB filter nb.year <= T guarantees it. Check monotone
    # non-decreasing cumulative counts across years for a sample of addresses instead.
    sample = df.sort_values(["address_id", "feature_year"]).groupby("address_id").head(60)
    bad = (sample.groupby("address_id")["ever_inside_perimeter"].diff() < 0).sum()
    if bad:
        problems.append(f"ever_inside_perimeter decreased over time for {int(bad)} rows")
    for c in ("wildfire_fhsz", "fhsz", "FHSZ"):
        if c in df.columns:
            problems.append(f"present-day classification column {c} must not be a feature")
    return problems

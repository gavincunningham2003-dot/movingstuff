import numpy as np
import pandas as pd

from catrix.ml.dataset import (
    DatasetSpec,
    assign_splits,
    build_location_year_table,
    feature_columns,
    leakage_checks,
)

SPEC = DatasetSpec(
    feature_years=(2000, 2006),
    train_years=(2000, 2003),
    val_years=(2004, 2004),
    test_years=(2005, 2006),
    spatial_holdout_fraction=0.5,
)


def _static(n=2):
    return pd.DataFrame(
        {
            "address_id": list(range(1, n + 1)),
            "lon": [-122.0, -120.0][:n],
            "lat": [37.0, 39.0][:n],
            "county_fips": ["06001", "06017"][:n],
            "elevation_m": [10.0, 500.0][:n],
            "slope_deg": [1.0, 20.0][:n],
            "aspect_deg": [90.0, 180.0][:n],
            "buildings_within_100m": [20, 2][:n],
            "spatial_block": ["A", "B"][:n],
        }
    )


def test_label_and_history_features_respect_time_ordering():
    # SYNTHETIC perimeters: address 1 is inside a perimeter in 2003 only; address 2 has one
    # perimeter 2 km away in 2001 and none later.
    nb = pd.DataFrame(
        {
            "address_id": [1, 2],
            "year": [2003, 2001],
            "gis_acres": [1000.0, 50.0],
            "distance_m": [0.0, 2000.0],
            "inside": [True, False],
        }
    )
    st = _static().assign(
        northness=np.cos(np.radians(_static()["aspect_deg"])),
        eastness=np.sin(np.radians(_static()["aspect_deg"])),
    )
    df = build_location_year_table(nb, st, SPEC)
    a1 = df[df["address_id"] == 1].set_index("feature_year")
    # Label is 1 only for feature year 2002 (target 2003).
    assert a1["label"].to_dict() == {2000: 0, 2001: 0, 2002: 1, 2003: 0, 2004: 0, 2005: 0, 2006: 0}
    # The 2003 perimeter must not appear in features before 2003.
    assert a1.loc[2002, "ever_inside_perimeter"] == 0 and a1.loc[2002, "n_perim_1km_10y"] == 0
    assert a1.loc[2003, "ever_inside_perimeter"] == 1 and a1.loc[2003, "n_perim_1km_10y"] == 1
    assert a1.loc[2002, "dist_nearest_perim_m"] == 10_000  # fill value = search radius
    assert a1.loc[2003, "years_since_perim_1km"] == 0 and a1.loc[2006, "years_since_perim_1km"] == 3
    a2 = df[df["address_id"] == 2].set_index("feature_year")
    assert a2["label"].sum() == 0
    assert a2.loc[2001, "n_perim_5km_10y"] == 1 and a2.loc[2001, "n_perim_1km_10y"] == 0
    assert a2.loc[2001, "acres_5km_5y"] == 50.0 and a2.loc[2006, "acres_5km_5y"] == 0.0
    assert leakage_checks(df) == []
    assert "elevation_m" in feature_columns(df) and "wildfire_fhsz" not in feature_columns(df)


def test_assign_splits_is_chronological_with_spatial_holdout():
    nb = pd.DataFrame(
        {"address_id": [], "year": [], "gis_acres": [], "distance_m": [], "inside": []}
    )
    st = _static().assign(northness=0.0, eastness=0.0)
    df = assign_splits(build_location_year_table(nb, st, SPEC), SPEC, seed=0)
    hold = set(df.attrs["holdout_blocks"])
    assert len(hold) == 1
    held = df[df["spatial_block"].isin(hold)]
    assert (held["split"] == "spatial_test").all()
    rest = df[~df["spatial_block"].isin(hold)]
    assert set(rest.loc[rest["feature_year"] <= 2003, "split"]) == {"train"}
    assert set(rest.loc[rest["feature_year"] == 2004, "split"]) == {"val"}
    assert set(rest.loc[rest["feature_year"] >= 2005, "split"]) == {"test"}


def test_leakage_checks_detect_present_day_columns():
    df = pd.DataFrame(
        {
            "feature_year": [2000],
            "target_year": [2001],
            "address_id": [1],
            "ever_inside_perimeter": [0],
            "wildfire_fhsz": ["Very High"],
        }
    )
    assert any("present-day" in p for p in leakage_checks(df))
    df2 = pd.DataFrame(
        {
            "feature_year": [2000],
            "target_year": [2002],
            "address_id": [1],
            "ever_inside_perimeter": [0],
        }
    )
    assert any("target_year" in p for p in leakage_checks(df2))

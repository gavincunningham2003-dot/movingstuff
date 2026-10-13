"""Trains on a SYNTHETIC location-year table (labelled as such) to exercise the training,
calibration, evaluation and SHAP code paths end-to-end. Metrics here say nothing about real
wildfire predictability."""

import json

import numpy as np
import pandas as pd
import pytest

from catrix.ml.dataset import DatasetSpec, assign_splits
from catrix.ml.evaluate import calibration_table, choose_threshold, evaluate
from catrix.ml.train import MIN_POSITIVES, train_all

SPEC = DatasetSpec(
    feature_years=(2000, 2009),
    train_years=(2000, 2005),
    val_years=(2006, 2007),
    test_years=(2008, 2009),
    spatial_holdout_fraction=0.2,
)


def _synthetic_table(n_addr=300, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for aid in range(n_addr):
        slope = rng.uniform(0, 40)
        hist = rng.poisson(1.5)
        block = f"B{aid % 10}"
        ever = 0
        for T in range(SPEC.feature_years[0], SPEC.feature_years[1] + 1):
            n5 = max(0, hist + rng.integers(-1, 2))
            ever = max(ever, int(n5 > 3))  # cumulative flag must never decrease over time
            logit = -4.5 + 0.06 * slope + 0.5 * n5
            p = 1 / (1 + np.exp(-logit))
            rows.append(
                {
                    "address_id": aid,
                    "feature_year": T,
                    "target_year": T + 1,
                    "label": int(rng.random() < p),
                    "n_perim_1km_10y": int(n5 > 2),
                    "n_perim_5km_10y": n5,
                    "n_perim_10km_10y": n5 + 1,
                    "n_perim_5km_30y": n5 + 2,
                    "years_since_perim_1km": float(rng.integers(0, 60)),
                    "ever_inside_perimeter": ever,
                    "acres_5km_5y": float(n5 * 100),
                    "dist_nearest_perim_m": float(rng.uniform(0, 10000)),
                    "elevation_m": float(rng.uniform(0, 2000)),
                    "slope_deg": slope,
                    "northness": float(rng.uniform(-1, 1)),
                    "eastness": float(rng.uniform(-1, 1)),
                    "buildings_within_100m": int(rng.integers(0, 30)),
                    "county_fips": f"06{aid % 5:03d}",
                    "spatial_block": block,
                    "lon": -121.0,
                    "lat": 38.0,
                    "dataset_version": "test",
                    "label_quality": "synthetic",
                }
            )
    return pd.DataFrame(rows)


def test_evaluate_and_threshold_helpers():
    y = np.array([0, 0, 1, 1, 0, 1, 0, 0])
    p = np.array([0.1, 0.2, 0.8, 0.6, 0.3, 0.9, 0.05, 0.4])
    thr = choose_threshold(y, p)
    d = evaluate(y, p, thr)
    assert d["metrics"]["roc_auc"] == 1.0 and d["metrics"]["recall"] == 1.0
    assert d["confusion"]["tp"] == 3 and d["confusion"]["fn"] == 0
    assert len(d["roc_curve"]) >= 2 and len(d["pr_curve"]) >= 2
    cal = calibration_table(y, p, n_bins=4)
    assert all(0 <= r["fraction_positive"] <= 1 for r in cal)


def test_insufficient_data_is_reported_not_fabricated(tmp_path):
    df = assign_splits(_synthetic_table(n_addr=20), SPEC, seed=0)
    df["label"] = 0
    df.loc[df.index[:5], "label"] = 1
    doc = train_all(df, SPEC)
    assert doc["status"] == "insufficient_data" and doc["models"] == []
    assert str(MIN_POSITIVES) in doc["status_detail"]


@pytest.mark.slow
def test_train_all_end_to_end_on_synthetic_data():
    df = assign_splits(_synthetic_table(), SPEC, seed=0)
    doc = train_all(df, SPEC, version="test-run")
    assert doc["status"] == "available"
    names = {m["name"] for m in doc["models"]}
    assert names == {
        "historical_frequency_baseline",
        "logistic_regression",
        "random_forest",
        "xgboost",
    }
    assert sum(m["is_selected"] for m in doc["models"]) == 1
    for m in doc["models"]:
        t = m["test"]["metrics"]
        assert 0 <= t["brier"] <= 1 and t["n"] > 0 and 0 < t["prevalence"] < 1
        assert len(m["test"]["calibration"]) >= 1
        if m["algorithm"] != "historical_frequency":
            assert m["feature_importance"] and m["shap_summary"]
            assert {r["feature"] for r in m["shap_summary"]} == set(doc["feature_list"])
    assert "spatial_test" in doc["models"][-1]
    from catrix.config import get_settings

    metrics = json.loads((get_settings().models_dir / "test-run" / "metrics.json").read_text())
    assert metrics["selected_model"] == doc["selected_model"]
    assert (get_settings().models_dir / "LATEST").read_text().strip() == "test-run"

"""Chronological cross-validation and portable artefacts (SYNTHETIC data only)."""

from __future__ import annotations

import json
import shutil

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression

from catrix.ml.cv import ChronologicalCVError, chronological_cv, chronological_folds


def test_folds_are_strictly_chronological_with_a_gap():
    folds = chronological_folds(range(1995, 2015), n_folds=3, gap_years=1, min_train_years=6)
    # 13 candidate years (2002-2014) split into 3 contiguous blocks, earlier blocks taking
    # the remainder
    assert [f.val_years for f in folds] == [(2002, 2006), (2007, 2010), (2011, 2014)]
    for f in folds:
        # training years end before the validation block starts, leaving the gap year out
        assert f.train_years[1] < f.val_years[0] - f.gap_years
        assert f.train_years[0] == 1995
        # the targets of training rows (T+1) never coincide with a validation feature year
        assert f.train_years[1] + 1 < f.val_years[0]


def test_folds_shrink_on_short_histories_and_refuse_impossible_ones():
    folds = chronological_folds(range(2000, 2006), n_folds=3, gap_years=1, min_train_years=2)
    assert all(f.train_years[1] < f.val_years[0] - 1 for f in folds)
    with pytest.raises(ChronologicalCVError):
        chronological_folds(range(2000, 2004), n_folds=3, gap_years=1, min_train_years=6)


def _table(seed: int = 0, years=range(1995, 2015), n_per_year: int = 300):
    rng = np.random.default_rng(seed)
    rows = []
    for y in years:
        x = rng.normal(size=n_per_year)
        p = 1 / (1 + np.exp(-(-3.5 + 1.2 * x)))
        rows.append(
            pd.DataFrame(
                {"x": x, "feature_year": y, "label": (rng.random(n_per_year) < p).astype(int)}
            )
        )
    return pd.concat(rows, ignore_index=True)


def test_chronological_cv_never_scores_on_training_rows():
    df = _table()
    folds = chronological_folds(
        df["feature_year"].unique(), n_folds=3, gap_years=1, min_train_years=6
    )
    res = chronological_cv(
        LogisticRegression(),
        df[["x"]],
        df["label"].to_numpy(),
        df["feature_year"].to_numpy(),
        folds,
    )
    assert 0 < res["mean_pr_auc"] <= 1
    used = [f for f in res["folds"] if f["status"] == "used"]
    assert used and all(f["train_years"][1] < f["val_years"][0] for f in used)
    # out-of-fold predictions exist only for validation-block rows
    oof_years = df["feature_year"].to_numpy()[res["oof_index"]]
    assert set(oof_years) <= {
        y for f in used for y in range(f["val_years"][0], f["val_years"][1] + 1)
    }


def test_chronological_cv_skips_sparse_folds_and_raises_when_none_usable():
    df = _table()
    df["label"] = 0  # no positives anywhere
    folds = chronological_folds(
        df["feature_year"].unique(), n_folds=3, gap_years=1, min_train_years=6
    )
    with pytest.raises(ChronologicalCVError):
        chronological_cv(
            LogisticRegression(),
            df[["x"]],
            df["label"].to_numpy(),
            df["feature_year"].to_numpy(),
            folds,
        )


def _train_tiny_run(tmp_path, monkeypatch, version: str):
    from catrix.config import get_settings
    from catrix.ml.dataset import assign_splits
    from catrix.ml.train import train_all
    from tests.unit.test_ml_train import SPEC, _synthetic_table

    df = assign_splits(_synthetic_table(n_addr=300), SPEC, seed=0)
    doc = train_all(df, SPEC, version=version)
    assert doc["status"] == "available"
    return doc, get_settings().models_dir


def test_artifacts_are_portable_and_validated(tmp_path, monkeypatch):
    from catrix.config import get_settings
    from catrix.ml.artifacts import ArtifactIncompatibleError, validate_document
    from catrix.ml.predict import load_model, reset_model_cache

    doc, models_dir = _train_tiny_run(tmp_path, monkeypatch, "wf-test-port")
    # metrics name artefacts by file, never by absolute path
    sel = next(m for m in doc["models"] if m["is_selected"])
    assert sel["artifact_file"].endswith(".joblib") and not sel["artifact_path"].startswith(
        str(models_dir)
    )
    assert doc["preprocessing_version"] and doc["feature_schema"] and doc["experiment_id"]
    assert doc["dependency_versions"]["scikit-learn"]
    assert validate_document(doc) == [] or all(
        p.startswith("warning:") for p in validate_document(doc)
    )

    # move the whole models directory somewhere else and load from there
    moved = tmp_path / "elsewhere" / "models"
    shutil.move(str(models_dir), str(moved))
    monkeypatch.setenv("CATRIX_DATA_DIR", str(tmp_path / "elsewhere"))
    get_settings.cache_clear()
    reset_model_cache()
    tm, loaded = load_model("wf-test-port")
    assert loaded["version"] == "wf-test-port" and tm.name == doc["selected_model"]

    # an incompatible preprocessing version is refused, never silently used
    mp = moved / "wf-test-port" / "metrics.json"
    bad = json.loads(mp.read_text(encoding="utf-8"))
    bad["preprocessing_version"] = "features-v0"
    mp.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ArtifactIncompatibleError):
        load_model("wf-test-port")


def test_model_selection_ignores_test_labels(tmp_path, monkeypatch):
    """Changing the test labels must not change which model is selected."""
    from catrix.ml.dataset import assign_splits
    from catrix.ml.train import train_all
    from tests.unit.test_ml_train import SPEC, _synthetic_table

    df = assign_splits(_synthetic_table(n_addr=300), SPEC, seed=0)
    a = train_all(df, SPEC, version="wf-sel-a", set_latest=False)
    flipped = df.copy()
    rng = np.random.default_rng(1)
    mask = (flipped["split"] == "test").to_numpy()
    flipped.loc[mask, "label"] = rng.integers(0, 2, size=mask.sum())
    b = train_all(flipped, SPEC, version="wf-sel-b", set_latest=False)
    assert a["selected_model"] == b["selected_model"]
    assert a["selection"]["cv_pr_auc_by_family"] == b["selection"]["cv_pr_auc_by_family"]

"""Training: historical-frequency baseline, logistic regression, random forest, XGBoost.

* Hyper-parameters are tuned on the validation years only (small grid; Optuna optional).
* Class imbalance is handled with class weights / ``scale_pos_weight`` — no synthetic
  oversampling, because rows are spatially and temporally dependent.
* Probabilities are post-hoc calibrated (isotonic) on the validation set; test years are
  untouched until the final evaluation.
* Every artefact is written with joblib under ``data/models/<version>/`` and summarised in
  ``metrics.json``; a ``ModelVersion`` row is created when a session is supplied.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy.orm import Session

from catrix.config import get_settings
from catrix.db.models import ModelVersion
from catrix.logging import get_logger
from catrix.ml.dataset import DEFAULT_SPEC, DatasetSpec, feature_columns, leakage_checks
from catrix.ml.evaluate import (
    OPERATING_POINT_LABELS,
    SHARE_POINTS,
    choose_threshold,
    confusion_at,
    confusion_top_share,
    evaluate,
    geographic_performance,
    operating_points,
    threshold_sweep,
)
from catrix.ml.explain import shap_summary
from catrix.ml.optional_features import describe_optional

log = get_logger(__name__)

TARGET_DEFINITION = (
    "Binary: address point lies inside >=1 CAL FIRE FRAP fire perimeter with YEAR_ = T+1, "
    "given features observable through 31 Dec of year T. Measures mapped perimeter exposure, "
    "not building damage."
)
TARGET_DEFINITIONS = {
    "inside": TARGET_DEFINITION,
    "within_1km": (
        "Binary: at least one CAL FIRE FRAP fire perimeter with YEAR_ = T+1 lies within 1 km of the "
        "address point, given features observable through 31 Dec of year T. A softer, "
        "proximity-based exposure target with roughly an order of magnitude more positives than "
        "the inside-perimeter target; still mapped-perimeter exposure, not damage."
    ),
}
MIN_POSITIVES = 100


class HistoricalFrequencyBaseline(BaseEstimator, ClassifierMixin):
    """P(fire next year) = smoothed historical annual rate of perimeter exposure within
    5 km over the previous 10 years, scaled by the training prevalence."""

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> HistoricalFrequencyBaseline:
        self.classes_ = np.array([0, 1])
        self.prior_ = float(np.mean(y))
        rate = X["n_perim_5km_10y"].to_numpy(float) / 10.0
        # Map rate → probability with a monotone logistic-like squash fitted by a 1-D
        # isotonic regression (keeps it a proper baseline, not a tuned model).
        self.iso_ = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(rate, y)
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        rate = X["n_perim_5km_10y"].to_numpy(float) / 10.0
        p = np.clip(self.iso_.predict(rate), 1e-6, 1 - 1e-6)
        return np.column_stack([1 - p, p])

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)


@dataclass
class TrainedModel:
    name: str
    algorithm: str
    estimator: Any
    calibrator: IsotonicRegression | None
    threshold: float
    params: dict[str, Any]

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        p = self.estimator.predict_proba(X)[:, 1]
        if self.calibrator is not None:
            p = self.calibrator.predict(p)
        return np.clip(p, 0.0, 1.0)

    def raw_scores(self, X: pd.DataFrame) -> np.ndarray:
        """Uncalibrated scores, for ranking (the calibrated probabilities are tied in steps)."""
        return np.asarray(self.estimator.predict_proba(X)[:, 1], dtype=float)


def _cv_average_precision(
    est: Any, X: pd.DataFrame, y: np.ndarray, groups: np.ndarray, n_splits: int = 3
) -> float:
    """Mean average precision over GroupKFold folds (groups = feature years), fitting a
    clone of ``est`` per fold. Falls back to a single fit/score when there are too few
    groups or a fold has no positives."""
    from sklearn.base import clone
    from sklearn.metrics import average_precision_score
    from sklearn.model_selection import GroupKFold

    n_groups = len(np.unique(groups))
    if n_groups < n_splits or y.sum() < n_splits:
        est.fit(X, y)
        return float(average_precision_score(y, est.predict_proba(X)[:, 1])) if y.sum() else 0.0
    scores = []
    for tr_idx, te_idx in GroupKFold(n_splits=n_splits).split(X, y, groups):
        if y[te_idx].sum() == 0 or y[tr_idx].sum() == 0:
            continue
        m = clone(est).fit(X.iloc[tr_idx], y[tr_idx])
        scores.append(
            float(average_precision_score(y[te_idx], m.predict_proba(X.iloc[te_idx])[:, 1]))
        )
    return float(np.mean(scores)) if scores else 0.0


def _pos_weight(y: np.ndarray) -> float:
    pos = max(int(y.sum()), 1)
    return float((len(y) - pos) / pos)


def _candidates(
    seed: int, pos_weight: float, device: str = "cpu"
) -> dict[str, list[tuple[str, Any, dict[str, Any]]]]:
    """Model name → list of (algorithm, estimator, params) to try on validation."""
    from xgboost import XGBClassifier

    lr = [
        (
            "logistic_regression",
            Pipeline(
                [
                    ("impute", SimpleImputer(strategy="median")),
                    ("scale", StandardScaler()),
                    (
                        "clf",
                        LogisticRegression(
                            C=c, class_weight="balanced", max_iter=2000, random_state=seed
                        ),
                    ),
                ]
            ),
            {"C": c},
        )
        for c in (0.1, 1.0)
    ]
    rf = [
        (
            "random_forest",
            Pipeline(
                [
                    ("impute", SimpleImputer(strategy="median")),
                    (
                        "clf",
                        RandomForestClassifier(
                            n_estimators=300,
                            max_depth=d,
                            min_samples_leaf=20,
                            class_weight="balanced_subsample",
                            n_jobs=-1,
                            random_state=seed,
                        ),
                    ),
                ]
            ),
            {"max_depth": d},
        )
        for d in (8, 14)
    ]
    xgb = [
        (
            "xgboost",
            XGBClassifier(
                n_estimators=600,
                learning_rate=lr_,
                max_depth=md,
                subsample=0.8,
                colsample_bytree=0.8,
                min_child_weight=5,
                reg_lambda=1.0,
                scale_pos_weight=pos_weight,
                objective="binary:logistic",
                eval_metric="aucpr",
                tree_method="hist",
                device=device,
                random_state=seed,
                n_jobs=-1,
            ),
            {"learning_rate": lr_, "max_depth": md},
        )
        for lr_, md in ((0.05, 4), (0.05, 6), (0.1, 4))
    ]
    return {"logistic_regression": lr, "random_forest": rf, "xgboost": xgb}


def train_all(
    df: pd.DataFrame,
    spec: DatasetSpec = DEFAULT_SPEC,
    *,
    session: Session | None = None,
    version: str | None = None,
    exclude_features: Iterable[str] | None = None,
    set_latest: bool = True,
) -> dict[str, Any]:
    """Train, select and evaluate. Returns the metrics document (also written to disk).

    ``exclude_features`` drops columns from the feature set (e.g. the ``*_version_lag_years``
    anachronism flags, which double as a calendar-year proxy). ``set_latest=False`` records
    the run without making it the model the API and ``catrix ml predict`` use."""
    settings = get_settings()
    seed = settings.random_seed
    np.random.seed(seed)
    problems = leakage_checks(df)
    if problems:
        raise ValueError(f"leakage checks failed: {problems}")
    excluded = sorted(set(exclude_features or ()))
    feats = [f for f in feature_columns(df) if f not in excluded]
    train, val, test, stest = (
        df[df["split"] == s] for s in ("train", "val", "test", "spatial_test")
    )
    n_pos_train = int(train["label"].sum())
    doc: dict[str, Any] = {
        "dataset_version": spec.version,
        "feature_list": feats,
        "target_definition": TARGET_DEFINITIONS.get(
            getattr(spec, "target", "inside"), TARGET_DEFINITION
        ),
        "target": getattr(spec, "target", "inside"),
        "excluded_features": excluded,
        "train_period": f"{spec.train_years[0]}-{spec.train_years[1]} (targets {spec.train_years[0]+1}-{spec.train_years[1]+1})",
        "validation_period": f"{spec.val_years[0]}-{spec.val_years[1]} (targets {spec.val_years[0]+1}-{spec.val_years[1]+1})",
        "test_period": f"{spec.test_years[0]}-{spec.test_years[1]} (targets {spec.test_years[0]+1}-{spec.test_years[1]+1})",
        "spatial_holdout": f"{len(df.attrs.get('holdout_blocks', []))} x 50 km blocks held out of train/val ({len(stest)} rows)",
        "n_train": int(len(train)),
        "n_val": int(len(val)),
        "n_test": int(len(test)),
        "n_spatial_test": int(len(stest)),
        "prevalence_train": float(train["label"].mean()) if len(train) else None,
        "prevalence_test": float(test["label"].mean()) if len(test) else None,
        "trained_at": datetime.now(UTC).isoformat(),
        "models": [],
        "status": "available",
        # Share of rows with a non-null value for each optional (climate/drought/fuel/
        # land-cover) feature, so readers can see which sources actually contributed.
        "optional_feature_coverage": describe_optional(df),
    }
    if n_pos_train < MIN_POSITIVES or len(test) == 0 or test["label"].sum() < 10:
        doc["status"] = "insufficient_data"
        doc["status_detail"] = (
            f"Only {n_pos_train} positive location-years in the training period and {int(test['label'].sum()) if len(test) else 0} in the test period. "
            f"CATRIX requires at least {MIN_POSITIVES} training positives and 10 test positives before reporting a supervised model. "
            "Increase the address sample, extend the feature-year range, or sample more fire-prone counties."
        )
        _write_metrics(doc, version or f"wf-{datetime.now(UTC):%Y%m%d%H%M}", set_latest=set_latest)
        return doc

    Xtr, ytr = train[feats], train["label"].to_numpy()
    Xva, yva = val[feats], val["label"].to_numpy()
    Xte, yte = test[feats], test["label"].to_numpy()
    pos_w = _pos_weight(ytr)
    results: list[tuple[TrainedModel, dict[str, Any]]] = []

    # Baseline (no tuning, no calibration beyond its own isotonic fit) --------------------
    base = HistoricalFrequencyBaseline().fit(Xtr, ytr)
    tm = TrainedModel(
        "historical_frequency_baseline",
        "historical_frequency",
        base,
        None,
        choose_threshold(yva, base.predict_proba(Xva)[:, 1]),
        {},
    )
    results.append((tm, _evaluate_model(tm, Xva, yva, Xte, yte, test, stest, feats)))

    # Candidates: pick the best params per family by PR-AUC under grouped cross-validation
    # INSIDE the training years (folds = blocks of feature years), so the validation years
    # are reserved for calibration and thresholds and the test years for reporting only.
    tune_groups = train["feature_year"].to_numpy()
    for family, cands in _candidates(seed, pos_w, settings.ml_device).items():
        best: tuple[float, TrainedModel] | None = None
        for algo, est, params in cands:
            ap = _cv_average_precision(est, Xtr, ytr, tune_groups)
            log.info("candidate_evaluated", family=family, params=params, cv_pr_auc=round(ap, 4))
            if best is None or ap > best[0]:
                best = (ap, TrainedModel(family, algo, est, None, 0.5, params))
        assert best is not None
        tm = best[1]
        tm.estimator.fit(Xtr, ytr)
        # Calibrate on validation, then choose the threshold on calibrated val probabilities.
        raw_val = tm.estimator.predict_proba(Xva)[:, 1]
        tm.calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(
            raw_val, yva
        )
        tm.threshold = choose_threshold(yva, tm.predict_proba(Xva))
        results.append((tm, _evaluate_model(tm, Xva, yva, Xte, yte, test, stest, feats)))

    # Selection: best VALIDATION PR-AUC among learned models, provided it beats the
    # baseline on validation. The test years play no part in choosing the model.
    base_doc = results[0][1]
    cand = [(m, d) for m, d in results[1:] if d["validation"]["metrics"]["pr_auc"] is not None]
    selected = (
        max(cand, key=lambda md: md[1]["validation"]["metrics"]["pr_auc"]) if cand else results[0]
    )
    if (
        base_doc["validation"]["metrics"]["pr_auc"] is not None
        and selected[1]["validation"]["metrics"]["pr_auc"]
        <= base_doc["validation"]["metrics"]["pr_auc"]
    ):
        doc["status_detail"] = (
            "No learned model beat the historical-frequency baseline on validation PR-AUC; "
            "the baseline is reported as selected."
        )
        selected = results[0]
    doc["selection"] = {
        "hyperparameters": "grouped 3-fold CV by feature year within the training years (PR-AUC)",
        "calibration": "isotonic regression fitted on the validation years",
        "thresholds": "chosen on the validation years",
        "model_choice": "validation PR-AUC; the test years and the spatial holdout are reported only",
    }
    ver = version or f"wf-{datetime.now(UTC):%Y%m%d%H%M}"
    out_dir = settings.models_dir / ver
    out_dir.mkdir(parents=True, exist_ok=True)
    for m, d in results:
        d["is_selected"] = m is selected[0]
        d["artifact_path"] = str(out_dir / f"{m.name}.joblib")
        joblib.dump(m, d["artifact_path"])
        doc["models"].append(d)
    doc["selected_model"] = selected[0].name
    doc["version"] = ver
    doc["is_latest"] = set_latest
    _write_metrics(doc, ver, set_latest=set_latest)
    if session is not None and set_latest:
        _persist_versions(session, doc)
    return doc


def _evaluate_model(
    tm: TrainedModel,
    Xva: pd.DataFrame,
    yva: np.ndarray,
    Xte: pd.DataFrame,
    yte: np.ndarray,
    test: pd.DataFrame,
    stest: pd.DataFrame,
    feats: list[str],
) -> dict[str, Any]:
    p_te = tm.predict_proba(Xte)
    p_va = tm.predict_proba(Xva)
    d: dict[str, Any] = {
        "name": tm.name,
        "algorithm": tm.algorithm,
        "params": tm.params,
        "threshold": tm.threshold,
        "validation": evaluate(yva, p_va, tm.threshold),
        "test": evaluate(yte, p_te, tm.threshold),
        "geographic_performance": geographic_performance(test, p_te, "county_fips"),
    }
    has_spatial = len(stest) > 0
    ys = stest["label"].to_numpy() if has_spatial else np.array([], dtype=int)
    ps = tm.predict_proba(stest[feats]) if has_spatial else np.array([], dtype=float)
    if has_spatial:
        d["spatial_test"] = evaluate(ys, ps, tm.threshold)
    # Alternative operating points (all chosen on validation) and a flagged-share sweep so
    # the dashboard can show the confusion matrix at any threshold.
    # Raw (uncalibrated) scores rank rows with far fewer ties than the isotonic
    # probabilities, so share-based points flag exactly the share they promise.
    r_va, r_te = tm.raw_scores(Xva), tm.raw_scores(Xte)
    r_s = tm.raw_scores(stest[feats]) if has_spatial else np.array([], dtype=float)
    ops = operating_points(yva, p_va)
    d["operating_points"] = [
        {
            "name": name,
            "label": OPERATING_POINT_LABELS.get(name, name),
            "threshold": thr,
            "validation": confusion_at(yva, p_va, thr),
            "test": confusion_at(yte, p_te, thr),
            **({"spatial_test": confusion_at(ys, ps, thr)} if has_spatial else {}),
        }
        for name, thr in ops.items()
    ] + [
        {
            "name": name,
            "label": OPERATING_POINT_LABELS.get(name, name),
            "threshold": confusion_top_share(yte, p_te, r_te, share)["threshold"],
            "validation": confusion_top_share(yva, p_va, r_va, share),
            "test": confusion_top_share(yte, p_te, r_te, share),
            **({"spatial_test": confusion_top_share(ys, ps, r_s, share)} if has_spatial else {}),
        }
        for name, share in SHARE_POINTS
    ]
    d["threshold_sweep"] = {
        "test": threshold_sweep(yte, p_te, r_te),
        **({"spatial_test": threshold_sweep(ys, ps, r_s)} if has_spatial else {}),
    }
    d["feature_importance"] = feature_importance(tm, feats)
    try:
        d["shap_summary"] = shap_summary(tm, Xte, feats, max_rows=2000)
    except Exception as exc:
        log.warning("shap_failed", model=tm.name, error=repr(exc))
        d["shap_summary"] = []
    return d


def feature_importance(tm: TrainedModel, feats: list[str]) -> list[dict[str, Any]]:
    est = tm.estimator
    clf = est.named_steps["clf"] if isinstance(est, Pipeline) else est
    if hasattr(clf, "feature_importances_"):
        vals = clf.feature_importances_
        kind = "gain/impurity"
    elif hasattr(clf, "coef_"):
        vals = np.abs(clf.coef_[0])
        kind = "|standardised coefficient|"
    else:
        return []
    return sorted(
        (
            {"feature": f, "importance": float(v), "kind": kind}
            for f, v in zip(feats, vals, strict=True)
        ),
        key=lambda r: -r["importance"],
    )


def _write_metrics(doc: dict[str, Any], version: str, *, set_latest: bool = True) -> Path:
    settings = get_settings()
    out_dir = settings.models_dir / version
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "metrics.json"
    p.write_text(json.dumps(doc, indent=2, default=float), encoding="utf-8")
    if set_latest:
        (settings.models_dir / "LATEST").write_text(version, encoding="utf-8")
    return p


def _persist_versions(session: Session, doc: dict[str, Any]) -> None:
    from sqlalchemy import update

    session.execute(update(ModelVersion).values(is_selected=False))
    for m in doc["models"]:
        session.add(
            ModelVersion(
                name=m["name"],
                version=f"{doc['version']}:{m['name']}",
                algorithm=m["algorithm"],
                trained_at=datetime.fromisoformat(doc["trained_at"]),
                dataset_version=doc["dataset_version"],
                target_definition=doc["target_definition"],
                train_period=doc["train_period"],
                validation_period=doc["validation_period"],
                test_period=doc["test_period"],
                spatial_holdout=doc["spatial_holdout"],
                artifact_path=m["artifact_path"],
                feature_list=doc["feature_list"],
                metrics=m,
                is_selected=bool(m["is_selected"]),
            )
        )
    session.flush()

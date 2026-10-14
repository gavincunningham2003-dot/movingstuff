"""Training: historical-frequency baseline, logistic regression, random forest, XGBoost.

Protocol (see docs/ML_METHODOLOGY.md):

1. **Tuning and algorithm choice** use chronological (forward-chaining) cross-validation
   *inside the training years* (:mod:`catrix.ml.cv`): every fold trains on years that
   strictly precede its validation block, with a gap so training targets (T+1) never
   coincide with validation feature years. The best parameters per family and the winning
   family are chosen by mean PR-AUC over usable folds. The baseline goes through the same
   folds and the winner must beat it there.
2. **Calibration** is fitted on the time-aware out-of-fold predictions of the chosen
   candidate (predictions for each validation block come from a model that never saw it),
   i.e. on data the calibrated model was not fitted to. Three options are compared on the
   *validation years* by Brier score: uncalibrated, isotonic, sigmoid (Platt).
3. **Operating thresholds** are read off the validation years.
4. **The test years and the spatial holdout are reported only.** They never influence
   tuning, selection, calibration or thresholds. Note that the test period has been
   inspected repeatedly during development; the metrics document says so.

Class imbalance is handled with class weights / ``scale_pos_weight`` only: rows are
spatially and temporally dependent, so synthetic oversampling would leak.

Every run is written as a portable directory ``data/models/<version>/`` (see
:mod:`catrix.ml.artifacts`).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
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
from sklearn.metrics import brier_score_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy.orm import Session

from catrix.config import get_settings
from catrix.db.models import ModelVersion
from catrix.logging import get_logger
from catrix.ml.artifacts import (
    PREPROCESSING_VERSION,
    dependency_versions,
    experiment_id,
)
from catrix.ml.cv import ChronologicalCVError, chronological_cv, chronological_folds
from catrix.ml.dataset import (
    DEFAULT_SPEC,
    DatasetSpec,
    feature_availability,
    feature_schema,
    leakage_checks,
    select_features,
)
from catrix.ml.evaluate import (
    OPERATING_POINT_LABELS,
    SHARE_POINTS,
    calibration_table,
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

EVALUATION_CAVEATS = [
    "The test years have been inspected repeatedly during development (multiple training runs "
    "on 2026-10-13 and 2026-10-14); treat test metrics as a final report on a fixed period, "
    "not as a never-seen holdout.",
    "Validation years are used twice without refitting any model: to choose the calibration "
    "method by Brier score and to read off operating thresholds.",
    "Location-years are spatially and temporally dependent; the spatial-block holdout is the "
    "better guide to performance in regions the model has not seen.",
]


class HistoricalFrequencyBaseline(BaseEstimator, ClassifierMixin):
    """P(fire next year) = smoothed historical annual rate of perimeter exposure within
    5 km over the previous 10 years, scaled by the training prevalence."""

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> HistoricalFrequencyBaseline:
        self.classes_ = np.array([0, 1])
        self.prior_ = float(np.mean(y))
        rate = X["n_perim_5km_10y"].to_numpy(float) / 10.0
        self.iso_ = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(rate, y)
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        rate = X["n_perim_5km_10y"].to_numpy(float) / 10.0
        p = np.clip(self.iso_.predict(rate), 1e-6, 1 - 1e-6)
        return np.column_stack([1 - p, p])

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)


class SigmoidCalibrator:
    """Platt scaling: logistic regression on the logit of the raw probability."""

    def __init__(self) -> None:
        self.lr_ = LogisticRegression(C=1e6, max_iter=1000)

    @staticmethod
    def _logit(p: np.ndarray) -> np.ndarray:
        p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p)).reshape(-1, 1)

    def fit(self, p: np.ndarray, y: np.ndarray) -> SigmoidCalibrator:
        self.lr_.fit(self._logit(p), np.asarray(y).astype(int))
        return self

    def predict(self, p: np.ndarray) -> np.ndarray:
        return self.lr_.predict_proba(self._logit(p))[:, 1]


@dataclass
class TrainedModel:
    name: str
    algorithm: str
    estimator: Any
    calibrator: Any | None
    threshold: float
    params: dict[str, Any]
    calibration_method: str = "none"

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        p = self.estimator.predict_proba(X)[:, 1]
        if self.calibrator is not None:
            p = self.calibrator.predict(p)
        return np.clip(p, 0.0, 1.0)

    def raw_scores(self, X: pd.DataFrame) -> np.ndarray:
        """Uncalibrated scores, for ranking (calibrated probabilities are tied in steps)."""
        return np.asarray(self.estimator.predict_proba(X)[:, 1], dtype=float)


@dataclass(frozen=True)
class CVConfig:
    n_folds: int = 3
    gap_years: int = 1
    min_train_years: int = 6
    min_pos_val: int = 5
    min_pos_train: int = 20


def _pos_weight(y: np.ndarray) -> float:
    pos = max(int(y.sum()), 1)
    return float((len(y) - pos) / pos)


def _candidates(
    seed: int, pos_weight: float, device: str = "cpu"
) -> dict[str, list[tuple[str, Any, dict[str, Any]]]]:
    """Model family -> list of (algorithm, estimator, params) to try."""
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


def _choose_calibration(
    oof_p: np.ndarray, oof_y: np.ndarray, raw_val: np.ndarray, yva: np.ndarray
) -> tuple[str, Any | None, dict[str, Any]]:
    """Fit isotonic and sigmoid calibrators on time-aware out-of-fold predictions and
    pick the method with the lowest Brier score on the validation years (uncalibrated is
    a candidate too). Returns (method, calibrator, report)."""
    options: dict[str, Any | None] = {"none": None}
    if len(oof_p) >= 50 and oof_y.sum() >= 5:
        options["isotonic"] = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(
            oof_p, oof_y
        )
        try:
            options["sigmoid"] = SigmoidCalibrator().fit(oof_p, oof_y)
        except Exception as exc:  # pragma: no cover - degenerate inputs
            log.warning("sigmoid_calibration_failed", error=repr(exc))
    report: dict[str, Any] = {
        "fitted_on": f"{len(oof_p)} out-of-fold rows ({int(oof_y.sum())} positives)"
    }
    scores: dict[str, float] = {}
    tables: dict[str, Any] = {}
    for name, cal in options.items():
        p = raw_val if cal is None else np.clip(cal.predict(raw_val), 0, 1)
        scores[name] = float(brier_score_loss(yva, p))
        tables[name] = calibration_table(yva, p)
    best = min(scores, key=lambda k: scores[k])
    report["brier_validation"] = scores
    report["reliability_validation"] = tables
    report["selected"] = best
    report["rule"] = "lowest Brier score on the validation years; ties favour 'none'"
    return best, options[best], report


def _fit_family(
    family: str,
    cands: list[tuple[str, Any, dict[str, Any]]],
    Xtr: pd.DataFrame,
    ytr: np.ndarray,
    years_tr: np.ndarray,
    folds: list,
    cfg: CVConfig,
) -> tuple[TrainedModel, dict[str, Any], dict[str, Any]] | None:
    """Tune one family by chronological CV; return the best (unfitted) model, its CV
    result and the per-candidate records. None if no candidate has a usable fold."""
    records: list[dict[str, Any]] = []
    best: tuple[float, TrainedModel, dict[str, Any]] | None = None
    for algo, est, params in cands:
        try:
            cv = chronological_cv(
                est,
                Xtr,
                ytr,
                years_tr,
                folds,
                min_pos_val=cfg.min_pos_val,
                min_pos_train=cfg.min_pos_train,
            )
        except ChronologicalCVError as exc:
            records.append(
                {"family": family, "params": params, "status": "no usable fold", "detail": str(exc)}
            )
            log.warning("candidate_unusable", family=family, params=params, error=str(exc))
            continue
        records.append(
            {
                "family": family,
                "params": params,
                "status": "scored",
                "cv_pr_auc": cv["mean_pr_auc"],
                "folds": [{k: v for k, v in f.items() if k != "gap_years"} for f in cv["folds"]],
            }
        )
        log.info(
            "candidate_evaluated",
            family=family,
            params=params,
            cv_pr_auc=round(cv["mean_pr_auc"], 4),
        )
        if best is None or cv["mean_pr_auc"] > best[0]:
            best = (cv["mean_pr_auc"], TrainedModel(family, algo, est, None, 0.5, params), cv)
    if best is None:
        return None
    return best[1], best[2], {"candidates": records}


def train_all(
    df: pd.DataFrame,
    spec: DatasetSpec = DEFAULT_SPEC,
    *,
    session: Session | None = None,
    version: str | None = None,
    exclude_features: Iterable[str] | None = None,
    set_latest: bool = True,
    feature_policy: str = "default",
    min_coverage: float = 0.5,
    cv_config: CVConfig | None = None,
) -> dict[str, Any]:
    """Train, select, calibrate and evaluate. Returns the metrics document (also written
    to disk as a portable run directory).

    ``feature_policy``: ``default`` (mandatory + optional features that meet
    ``min_coverage``), ``all`` (also experimental features), ``mandatory`` (history and
    terrain only). ``exclude_features`` removes named columns on top of the policy.
    ``set_latest=False`` records the run without making it the live model."""
    settings = get_settings()
    seed = settings.random_seed
    np.random.seed(seed)
    cfg = cv_config or CVConfig()
    problems = leakage_checks(df)
    if problems:
        raise ValueError(f"leakage checks failed: {problems}")
    excluded = sorted(set(exclude_features or ()))
    selection = select_features(df, policy=feature_policy, min_coverage=min_coverage)
    feats = [f for f in selection["features"] if f not in excluded]
    train, val, test, stest = (
        df[df["split"] == s] for s in ("train", "val", "test", "spatial_test")
    )
    n_pos_train = int(train["label"].sum())
    target = getattr(spec, "target", "inside")
    training_config = {
        "dataset": {**asdict(spec)},
        "target": target,
        "feature_policy": feature_policy,
        "min_coverage": min_coverage,
        "excluded_features": excluded,
        "features": feats,
        "seed": seed,
        "ml_device": settings.ml_device,
        "cv": asdict(cfg),
        "preprocessing_version": PREPROCESSING_VERSION,
    }
    doc: dict[str, Any] = {
        "dataset_version": spec.version,
        "feature_list": feats,
        "feature_schema": feature_schema(df, feats),
        "feature_availability": feature_availability(df),
        "feature_selection": {k: v for k, v in selection.items() if k != "features"},
        "target_definition": TARGET_DEFINITIONS.get(target, TARGET_DEFINITION),
        "target": target,
        "excluded_features": excluded,
        "train_period": f"{spec.train_years[0]}-{spec.train_years[1]} (targets {spec.train_years[0] + 1}-{spec.train_years[1] + 1})",
        "validation_period": f"{spec.val_years[0]}-{spec.val_years[1]} (targets {spec.val_years[0] + 1}-{spec.val_years[1] + 1})",
        "test_period": f"{spec.test_years[0]}-{spec.test_years[1]} (targets {spec.test_years[0] + 1}-{spec.test_years[1] + 1})",
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
        "optional_feature_coverage": describe_optional(df),
        "training_config": training_config,
        "experiment_id": experiment_id({**training_config, "dataset_version": spec.version}),
        "preprocessing_version": PREPROCESSING_VERSION,
        "dependency_versions": dependency_versions(),
        "evaluation_caveats": EVALUATION_CAVEATS,
    }
    ver = version or f"wf-{datetime.now(UTC):%Y%m%d%H%M}"
    if n_pos_train < MIN_POSITIVES or len(test) == 0 or test["label"].sum() < 10:
        doc["status"] = "insufficient_data"
        doc["status_detail"] = (
            f"Only {n_pos_train} positive location-years in the training period and "
            f"{int(test['label'].sum()) if len(test) else 0} in the test period. CATRIX requires at "
            f"least {MIN_POSITIVES} training positives and 10 test positives before reporting a "
            "supervised model. Increase the address sample, extend the feature-year range, or "
            "sample more fire-prone counties."
        )
        doc["version"] = ver
        _write_metrics(doc, ver, set_latest=set_latest)
        return doc

    Xtr, ytr = train[feats], train["label"].to_numpy()
    Xva, yva = val[feats], val["label"].to_numpy()
    Xte, yte = test[feats], test["label"].to_numpy()
    years_tr = train["feature_year"].to_numpy()
    try:
        folds = chronological_folds(
            years_tr,
            n_folds=cfg.n_folds,
            gap_years=cfg.gap_years,
            min_train_years=cfg.min_train_years,
        )
    except ChronologicalCVError:
        # Short training periods (e.g. the modern-era experiment): fewer, smaller folds.
        folds = chronological_folds(years_tr, n_folds=2, gap_years=cfg.gap_years, min_train_years=2)
    doc["cv_folds"] = [asdict(f) for f in folds]
    pos_w = _pos_weight(ytr)
    results: list[tuple[TrainedModel, dict[str, Any]]] = []

    # Baseline through the same folds (for the comparison) --------------------------------
    base_est = HistoricalFrequencyBaseline()
    try:
        base_cv = chronological_cv(
            base_est,
            Xtr,
            ytr,
            years_tr,
            folds,
            min_pos_val=cfg.min_pos_val,
            min_pos_train=cfg.min_pos_train,
        )
        base_cv_ap: float | None = base_cv["mean_pr_auc"]
        base_folds = base_cv["folds"]
    except ChronologicalCVError as exc:
        base_cv_ap, base_folds = None, [{"status": "no usable fold", "detail": str(exc)}]
    base = HistoricalFrequencyBaseline().fit(Xtr, ytr)
    tm = TrainedModel(
        "historical_frequency_baseline",
        "historical_frequency",
        base,
        None,
        choose_threshold(yva, base.predict_proba(Xva)[:, 1]),
        {},
    )
    d = _evaluate_model(tm, Xva, yva, Xte, yte, test, stest, feats)
    d["cv"] = {"mean_pr_auc": base_cv_ap, "folds": base_folds}
    results.append((tm, d))

    # Candidates: tuned and compared by chronological CV in the training years ------------
    cv_report: dict[str, Any] = {"config": asdict(cfg), "folds": doc["cv_folds"], "families": {}}
    family_cv: dict[str, float] = {}
    for family, cands in _candidates(seed, pos_w, settings.ml_device).items():
        fitted = _fit_family(family, cands, Xtr, ytr, years_tr, folds, cfg)
        if fitted is None:
            cv_report["families"][family] = {"status": "no usable fold"}
            continue
        tm, cv, cand_rec = fitted
        cv_report["families"][family] = {
            "best_params": tm.params,
            "cv_pr_auc": cv["mean_pr_auc"],
            **cand_rec,
        }
        family_cv[family] = cv["mean_pr_auc"]
        tm.estimator.fit(Xtr, ytr)
        raw_val = tm.raw_scores(Xva)
        method, cal, cal_report = _choose_calibration(cv["oof_p"], cv["oof_y"], raw_val, yva)
        tm.calibrator, tm.calibration_method = cal, method
        tm.threshold = choose_threshold(yva, tm.predict_proba(Xva))
        d = _evaluate_model(tm, Xva, yva, Xte, yte, test, stest, feats)
        d["cv"] = {"mean_pr_auc": cv["mean_pr_auc"], "folds": cv["folds"]}
        d["calibration"] = {"method": method, **cal_report}
        results.append((tm, d))
    doc["cv"] = cv_report

    # Selection: best family by chronological-CV PR-AUC; must beat the baseline on the
    # same folds. Validation years choose calibration method + thresholds only; the test
    # years and the spatial holdout are reported only.
    selected = results[0]
    if family_cv:
        best_family = max(family_cv, key=lambda k: family_cv[k])
        cand = next((md for md in results[1:] if md[0].name == best_family), None)
        if cand is not None and (base_cv_ap is None or family_cv[best_family] > base_cv_ap):
            selected = cand
        else:
            doc["status_detail"] = (
                "No learned model beat the historical-frequency baseline under chronological "
                "cross-validation; the baseline is reported as selected."
            )
    doc["selection"] = {
        "hyperparameters": "chronological (forward-chaining) CV inside the training years, mean PR-AUC",
        "algorithm": "family with the highest chronological-CV PR-AUC, if it beats the baseline on the same folds",
        "calibration": "fitted on time-aware out-of-fold predictions; method chosen by Brier score on the validation years",
        "thresholds": "chosen on the validation years",
        "test_and_spatial_holdout": "reported only",
        "cv_pr_auc_by_family": family_cv,
        "baseline_cv_pr_auc": base_cv_ap,
    }
    out_dir = settings.models_dir / ver
    out_dir.mkdir(parents=True, exist_ok=True)
    for m, d in results:
        d["is_selected"] = m is selected[0]
        d["artifact_file"] = f"{m.name}.joblib"
        d["artifact_path"] = f"{ver}/{m.name}.joblib"  # relative to the models directory
        d["calibration_method"] = m.calibration_method
        joblib.dump(m, out_dir / d["artifact_file"])
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

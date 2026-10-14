"""SHAP explanations (global summary and per-row contributions)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline


def _explainer_and_matrix(tm: Any, X: pd.DataFrame, feats: list[str]) -> tuple[Any, np.ndarray]:
    import shap

    est = tm.estimator
    if isinstance(est, Pipeline):
        clf = est.named_steps["clf"]
        pre = Pipeline(est.steps[:-1])
        Xm = pre.transform(X[feats])
    else:
        clf = est
        Xm = X[feats].to_numpy(float)
    name = type(clf).__name__
    if name in ("XGBClassifier", "RandomForestClassifier", "LGBMClassifier"):
        return shap.TreeExplainer(clf), Xm
    if name == "LogisticRegression":
        return shap.LinearExplainer(clf, Xm), Xm
    raise ValueError(f"no SHAP explainer for {name}")


def _values(explainer: Any, Xm: np.ndarray) -> tuple[np.ndarray, float]:
    sv = explainer.shap_values(Xm)
    ev = explainer.expected_value
    if isinstance(sv, list):  # RandomForest returns [class0, class1]
        sv = sv[1]
        ev = ev[1] if np.ndim(ev) else ev
    if np.ndim(sv) == 3:
        sv = sv[:, :, 1]
        ev = ev[1]
    return np.asarray(sv), float(np.asarray(ev).ravel()[0])


def shap_summary(
    tm: Any, X: pd.DataFrame, feats: list[str], max_rows: int = 2000
) -> list[dict[str, Any]]:
    if tm.algorithm == "historical_frequency":
        return []
    Xs = X.sample(n=min(max_rows, len(X)), random_state=0) if len(X) > max_rows else X
    explainer, Xm = _explainer_and_matrix(tm, Xs, feats)
    sv, _ = _values(explainer, Xm)
    mean_abs = np.abs(sv).mean(axis=0)
    mean_signed = sv.mean(axis=0)
    return sorted(
        (
            {"feature": f, "mean_abs_shap": float(a), "mean_shap": float(s)}
            for f, a, s in zip(feats, mean_abs, mean_signed, strict=True)
        ),
        key=lambda r: -r["mean_abs_shap"],
    )


def shap_for_rows(
    tm: Any, X: pd.DataFrame, feats: list[str]
) -> tuple[list[list[dict[str, Any]]], float]:
    """Per-row contributions in the model's raw output space (log-odds for XGBoost /
    logistic regression, probability for random forest). The API labels the space."""
    if tm.algorithm == "historical_frequency":
        return [[] for _ in range(len(X))], float("nan")
    explainer, Xm = _explainer_and_matrix(tm, X, feats)
    sv, base = _values(explainer, Xm)
    rows = []
    for i in range(len(X)):
        contribs = [
            {
                "feature": f,
                "value": (None if pd.isna(X[f].iloc[i]) else float(X[f].iloc[i])),
                "contribution": float(sv[i, j]),
            }
            for j, f in enumerate(feats)
        ]
        rows.append(sorted(contribs, key=lambda c: -abs(float(c["contribution"] or 0.0))))
    return rows, base

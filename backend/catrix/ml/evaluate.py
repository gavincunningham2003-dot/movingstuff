"""Evaluation metrics, curves, calibration and geographic breakdowns."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)


def _downsample_curve(x: np.ndarray, y: np.ndarray, n: int = 200) -> list[dict[str, float]]:
    idx = np.arange(len(x)) if len(x) <= n else np.unique(np.linspace(0, len(x) - 1, n).astype(int))
    return [{"x": float(x[i]), "y": float(y[i])} for i in idx]


def calibration_table(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> list[dict[str, float]]:
    # Quantile bins so that rare-event probabilities are not all in the first bin.
    edges = np.unique(np.quantile(p, np.linspace(0, 1, n_bins + 1)))
    if len(edges) < 3:
        edges = np.linspace(0, max(p.max(), 1e-6), n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, len(edges) - 2)
    rows = []
    for b in range(len(edges) - 1):
        m = idx == b
        if m.sum() == 0:
            continue
        rows.append(
            {
                "bin": float(b),
                "mean_predicted": float(p[m].mean()),
                "fraction_positive": float(y[m].mean()),
                "n": float(m.sum()),
            }
        )
    return rows


def choose_threshold(y_val: np.ndarray, p_val: np.ndarray) -> float:
    """Threshold maximising F1 on the validation set (documented choice; the dashboard
    shows the full PR curve so users can pick their own operating point)."""
    prec, rec, thr = precision_recall_curve(y_val, p_val)
    f1 = 2 * prec[:-1] * rec[:-1] / np.clip(prec[:-1] + rec[:-1], 1e-12, None)
    if len(thr) == 0:
        return 0.5
    return float(thr[int(np.nanargmax(f1))])


def evaluate(y: np.ndarray, p: np.ndarray, threshold: float) -> dict[str, Any]:
    y = np.asarray(y).astype(int)
    p = np.asarray(p).astype(float)
    pred = (p >= threshold).astype(int)
    has_both = len(np.unique(y)) == 2
    fpr, tpr, _ = roc_curve(y, p) if has_both else (np.array([0, 1]), np.array([0, 1]), None)
    prec, rec, _ = (
        precision_recall_curve(y, p) if has_both else (np.array([1, 0]), np.array([0, 1]), None)
    )
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return {
        "metrics": {
            "roc_auc": float(roc_auc_score(y, p)) if has_both else None,
            "pr_auc": float(average_precision_score(y, p)) if has_both else None,
            "precision": float(precision_score(y, pred, zero_division=0)),
            "recall": float(recall_score(y, pred, zero_division=0)),
            "f1": float(f1_score(y, pred, zero_division=0)),
            "brier": float(brier_score_loss(y, p)),
            "prevalence": float(y.mean()),
            "n": int(len(y)),
        },
        "threshold": float(threshold),
        "confusion": {"tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn)},
        "roc_curve": [{"fpr": d["x"], "tpr": d["y"]} for d in _downsample_curve(fpr, tpr)],
        "pr_curve": [{"recall": d["x"], "precision": d["y"]} for d in _downsample_curve(rec, prec)],
        "calibration": calibration_table(y, p),
    }


def geographic_performance(
    df: pd.DataFrame, p: np.ndarray, group_col: str, min_n: int = 200
) -> list[dict[str, Any]]:
    out = []
    tmp = df[[group_col, "label"]].copy()
    tmp["p"] = p
    for g, sub in tmp.groupby(group_col):
        if len(sub) < min_n:
            continue
        y = sub["label"].to_numpy()
        both = len(np.unique(y)) == 2
        out.append(
            {
                "region": str(g),
                "n": int(len(sub)),
                "prevalence": float(y.mean()),
                "roc_auc": float(roc_auc_score(y, sub["p"])) if both else None,
                "pr_auc": float(average_precision_score(y, sub["p"])) if both else None,
            }
        )
    return sorted(out, key=lambda r: -r["n"])

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


SWEEP_SHARES = (0.001, 0.002, 0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5)


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95 % Wilson score interval for a proportion k/n (None when n == 0)."""
    if n <= 0:
        return None
    ph = k / n
    denom = 1 + z * z / n
    centre = (ph + z * z / (2 * n)) / denom
    half = z * ((ph * (1 - ph) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def confusion_at(y: np.ndarray, p: np.ndarray, threshold: float) -> dict[str, Any]:
    """Counts and rates at one threshold (flagged = p >= threshold)."""
    y = np.asarray(y).astype(int)
    p = np.asarray(p).astype(float)
    pred = (p >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    n = max(len(y), 1)
    return {
        "threshold": float(threshold),
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
        "tn": int(tn),
        "flagged_share": float((tp + fp) / n),
        "precision": float(tp / (tp + fp)) if tp + fp else None,
        "recall": float(tp / (tp + fn)) if tp + fn else None,
        "specificity": float(tn / (tn + fp)) if tn + fp else None,
        "lift": float((tp / (tp + fp)) / (y.mean() or 1e-12)) if tp + fp and y.mean() else None,
        "recall_ci95": wilson_interval(int(tp), int(tp + fn)),
        "n_positives": int(tp + fn),
        # A probability threshold fixed on the validation years and applied unchanged.
        "kind": "fixed_threshold",
    }


def confusion_top_share(
    y: np.ndarray, p: np.ndarray, rank_score: np.ndarray, share: float
) -> dict[str, Any]:
    """Flag exactly the top ``share`` of rows by ``rank_score`` (the uncalibrated model
    score, which has far fewer ties than isotonic-calibrated probabilities) and report the
    counts; ``threshold`` is the smallest calibrated probability among the flagged rows."""
    y = np.asarray(y).astype(int)
    p = np.asarray(p).astype(float)
    r = np.asarray(rank_score).astype(float)
    n = len(y)
    k = int(round(n * share))
    flagged = np.zeros(n, dtype=bool)
    if k > 0:
        order = np.argsort(-r, kind="stable")
        flagged[order[:k]] = True
    tp = int((flagged & (y == 1)).sum())
    fp = int((flagged & (y == 0)).sum())
    fn = int((~flagged & (y == 1)).sum())
    tn = int((~flagged & (y == 0)).sum())
    prev = y.mean() if n else 0.0
    return {
        "threshold": float(p[flagged].min()) if k > 0 else float(p.max() if n else 1.0),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "flagged_share": float(k / n) if n else 0.0,
        "precision": float(tp / (tp + fp)) if tp + fp else None,
        "recall": float(tp / (tp + fn)) if tp + fn else None,
        "specificity": float(tn / (tn + fp)) if tn + fp else None,
        "lift": float((tp / (tp + fp)) / prev) if tp + fp and prev else None,
        "recall_ci95": wilson_interval(tp, tp + fn),
        "n_positives": int(tp + fn),
        # A review budget: the top ``share`` of THIS evaluation sample by model rank.
        # It is not a transferable probability threshold.
        "kind": "ranking_budget",
    }


def threshold_sweep(
    y: np.ndarray, p: np.ndarray, rank_score: np.ndarray | None = None
) -> list[dict[str, Any]]:
    """Confusion counts when flagging a fixed share of rows (0.1 % ... 50 %), ranked by the
    raw score when given, so the dashboard can show any operating point."""
    p = np.asarray(p).astype(float)
    if len(p) == 0:
        return []
    r = p if rank_score is None else np.asarray(rank_score).astype(float)
    out = []
    for share in SWEEP_SHARES:
        row = confusion_top_share(y, p, r, share)
        row["target_share"] = share
        out.append(row)
    return out


def operating_points(y_val: np.ndarray, p_val: np.ndarray) -> dict[str, float]:
    """Named probability thresholds chosen on the validation set only: the F1-optimal point
    and points that reach a target recall. Share-based points ('flag the riskiest N %') are
    handled by rank, see :func:`confusion_top_share`."""
    pts = {"f1_validation": choose_threshold(y_val, p_val)}
    p_val = np.asarray(p_val).astype(float)
    prec, rec, thr = precision_recall_curve(y_val, p_val)
    for name, target in (("recall_25", 0.25), ("recall_50", 0.5), ("recall_75", 0.75)):
        idx = np.where(rec[:-1] >= target)[0]
        if len(thr) == 0:
            pts[name] = 0.5
        elif len(idx) == 0:
            pts[name] = float(thr.min())
        else:
            pts[name] = float(thr[idx].max())
    return pts


SHARE_POINTS = (("top_1pct", 0.01), ("top_5pct", 0.05), ("top_10pct", 0.10))


OPERATING_POINT_LABELS = {
    "f1_validation": "Fixed threshold: F1-optimal on validation",
    "recall_25": "Fixed threshold: recall >= 25 % on validation",
    "recall_50": "Fixed threshold: recall >= 50 % on validation",
    "recall_75": "Fixed threshold: recall >= 75 % on validation",
    "top_1pct": "Review budget: riskiest 1 % of this sample",
    "top_5pct": "Review budget: riskiest 5 % of this sample",
    "top_10pct": "Review budget: riskiest 10 % of this sample",
}


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

"""Chronological (forward-chaining) cross-validation for the location-year table.

The target of a row with feature year T is the perimeter exposure in T+1, so plain
GroupKFold over years is not enough: a fold could train on 2010 and validate on 2003.
Here every fold's training years strictly precede its validation years, with a gap so
that the *targets* of the training rows (T+1) never coincide with the feature years of the
validation rows.

Example with feature years 1995-2014, three folds, a one-year gap and at least six
training years: validation blocks 2002-2005, 2006-2009, 2010-2014, trained on
1995-2000, 1995-2004 and 1995-2008 respectively.

Folds without enough positives are reported and skipped; if no fold is usable the caller
gets an error rather than a score computed on training data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.metrics import average_precision_score

MIN_POS_VAL = 5
MIN_POS_TRAIN = 20


class ChronologicalCVError(ValueError):
    """No usable fold (e.g. too few years or positives)."""


@dataclass(frozen=True)
class Fold:
    index: int
    train_years: tuple[int, int]  # inclusive
    val_years: tuple[int, int]  # inclusive
    gap_years: int

    def train_mask(self, years: np.ndarray) -> np.ndarray:
        return (years >= self.train_years[0]) & (years <= self.train_years[1])

    def val_mask(self, years: np.ndarray) -> np.ndarray:
        return (years >= self.val_years[0]) & (years <= self.val_years[1])


def chronological_folds(
    years: list[int] | np.ndarray,
    *,
    n_folds: int = 3,
    gap_years: int = 1,
    min_train_years: int = 6,
) -> list[Fold]:
    """Expanding-window folds over the sorted unique ``years``.

    The last ``len(years) - min_train_years - gap_years`` years are split into
    ``n_folds`` contiguous validation blocks (fewer when there are not enough years); each
    block is validated by a model trained on every year before ``block_start - gap_years``.
    """
    ys = sorted({int(y) for y in years})
    if n_folds < 1:
        raise ChronologicalCVError("n_folds must be >= 1")
    first_val_idx = min_train_years + gap_years
    candidates = ys[first_val_idx:]
    if len(candidates) == 0:
        raise ChronologicalCVError(
            f"need more than {min_train_years + gap_years} distinct feature years for "
            f"chronological CV (got {len(ys)}: {ys[0] if ys else None}-{ys[-1] if ys else None})"
        )
    k = min(n_folds, len(candidates))
    blocks = [list(b) for b in np.array_split(np.array(candidates), k) if len(b)]
    folds: list[Fold] = []
    for i, block in enumerate(blocks):
        v0, v1 = int(block[0]), int(block[-1])
        t1 = v0 - gap_years - 1
        train_years = [y for y in ys if y <= t1]
        if not train_years:
            continue
        folds.append(Fold(i, (train_years[0], train_years[-1]), (v0, v1), gap_years))
    if not folds:
        raise ChronologicalCVError("no fold has a non-empty training period")
    return folds


def chronological_cv(
    est: Any,
    X: pd.DataFrame,
    y: np.ndarray,
    years: np.ndarray,
    folds: list[Fold],
    *,
    min_pos_val: int = MIN_POS_VAL,
    min_pos_train: int = MIN_POS_TRAIN,
) -> dict[str, Any]:
    """Fit a clone of ``est`` per fold on the fold's training years and score it on the
    validation years. Returns the mean average precision over usable folds, a record of
    every fold (including skipped ones and why), and the time-aware out-of-fold
    predictions (index positions, raw probabilities, labels) for calibration.

    Raises :class:`ChronologicalCVError` when no fold is usable: a candidate is never
    scored on its own training data as a fallback.
    """
    y = np.asarray(y).astype(int)
    years = np.asarray(years).astype(int)
    records: list[dict[str, Any]] = []
    aps: list[float] = []
    oof_idx: list[np.ndarray] = []
    oof_p: list[np.ndarray] = []
    for f in folds:
        tr, va = f.train_mask(years), f.val_mask(years)
        rec: dict[str, Any] = {
            **asdict(f),
            "n_train": int(tr.sum()),
            "n_val": int(va.sum()),
            "pos_train": int(y[tr].sum()),
            "pos_val": int(y[va].sum()),
        }
        if rec["pos_train"] < min_pos_train or rec["pos_val"] < min_pos_val:
            rec["status"] = "skipped"
            rec["reason"] = (
                f"needs >= {min_pos_train} training and >= {min_pos_val} validation positives"
            )
            records.append(rec)
            continue
        model = clone(est).fit(X[tr], y[tr])
        p = np.asarray(model.predict_proba(X[va])[:, 1], dtype=float)
        ap = float(average_precision_score(y[va], p))
        rec["status"] = "used"
        rec["pr_auc"] = ap
        records.append(rec)
        aps.append(ap)
        oof_idx.append(np.flatnonzero(va))
        oof_p.append(p)
    if not aps:
        raise ChronologicalCVError(
            "no usable chronological fold: "
            + "; ".join(f"{r['val_years']}: {r.get('reason', '')}" for r in records)
        )
    idx = np.concatenate(oof_idx)
    return {
        "mean_pr_auc": float(np.mean(aps)),
        "folds": records,
        "oof_index": idx,
        "oof_p": np.concatenate(oof_p),
        "oof_y": y[idx],
    }

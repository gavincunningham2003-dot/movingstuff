"""Transparent, exploratory composite screening index (0-100).

**This is a prioritisation score, not a calibrated probability or a premium.**

Method (version ``cs-1.0``):

1. Each contributing hazard is normalised to [0, 1] with a documented, hazard-specific
   function (``NORMALISERS``) applied to the module's quantitative indicator.
2. Hazards are grouped into *peril families* so correlated indicators (e.g. FHSZ class and
   perimeter history, PGA and liquefaction) are combined within the family by a max/mean
   rule instead of being summed as if independent.
3. Family scores are combined with explicit weights (``FAMILY_WEIGHTS``). Weights are
   **assumptions** chosen for screening; they are not estimated from loss outcomes.
4. Missing (unavailable / outside_coverage) hazards are excluded from the denominator and
   listed so the user can see what the score does *not* reflect. If more than half of the
   weight is missing, the score is withheld.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from catrix.hazards.base import HazardResult

METHOD_VERSION = "cs-1.0"

WEIGHTS_NOTE = (
    "Family weights (wildfire 0.35, earthquake 0.30, flood 0.20, geotechnical 0.10, "
    "regional 0.05) are screening assumptions chosen by the CATRIX authors; they are NOT "
    "estimated from loss data and must not be read as relative loss contribution."
)

FAMILY_WEIGHTS: dict[str, float] = {
    "wildfire": 0.35,
    "earthquake": 0.30,
    "flood": 0.20,
    "geotechnical": 0.10,
    "regional": 0.05,
}

FAMILIES: dict[str, list[str]] = {
    "wildfire": ["wildfire_fhsz", "wildfire_history", "wildfire_ml"],
    "earthquake": ["earthquake_pga"],
    "flood": ["flood_nfhl", "tsunami", "coastal_flooding"],
    "geotechnical": ["landslide", "liquefaction"],
    "regional": ["windstorm", "drought", "extreme_heat", "winter_storm", "extreme_rainfall"],
}

# Within-family combination: wildfire/flood/geotechnical take the max (overlapping
# indicators of the same peril should not add), regional takes the mean.
FAMILY_RULE: dict[str, str] = {
    "wildfire": "max",
    "earthquake": "max",
    "flood": "max",
    "geotechnical": "max",
    "regional": "mean",
}


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _fhsz(r: HazardResult) -> float | None:
    if r.coverage_status == "not_in_hazard_area":
        return 0.0
    return None if r.value is None else _clip(r.value / 3.0)


def _wf_history(r: HazardResult) -> float | None:
    if r.coverage_status not in ("covered", "not_in_hazard_area"):
        return None
    inside = bool(r.details.get("inside_any_perimeter"))
    n = r.value or 0.0
    return _clip(max(0.6 if inside else 0.0, min(n, 10) / 10.0 * 0.8))


def _wf_ml(r: HazardResult) -> float | None:
    # Model probability of perimeter exposure next year. Scaled so that 10% → 1.0 because
    # annual perimeter exposure above ~10% is extreme; documented in ML_METHODOLOGY.md.
    return None if r.value is None else _clip(r.value / 0.10)


def _pga(r: HazardResult) -> float | None:
    return None if r.value is None else _clip(r.value / 1.0)  # 1.0 g → saturation


def _flood(r: HazardResult) -> float | None:
    if r.coverage_status == "not_in_hazard_area":
        return None  # unmapped region: unknown, not zero
    if r.value is None:
        return None
    return {3.0: 1.0, 2.0: 0.4, 1.0: 0.05, 0.0: None}.get(r.value, None)


def _binary(r: HazardResult) -> float | None:
    if r.coverage_status == "not_in_hazard_area":
        return 0.0
    if r.coverage_status == "covered":
        return 1.0
    return None


def _landslide(r: HazardResult) -> float | None:
    if r.coverage_status == "not_in_hazard_area":
        return 0.0
    return None if r.value is None else _clip(r.value / 10.0)


def _nri(r: HazardResult) -> float | None:
    return None if r.value is None else _clip(r.value / 100.0)


def _rain(r: HazardResult) -> float | None:
    # 100-yr 24-h depth in California spans ~50 mm (deserts) to ~400 mm (coastal ranges).
    return None if r.value is None else _clip((r.value - 50.0) / 350.0)


NORMALISERS: dict[str, Callable[[HazardResult], float | None]] = {
    "wildfire_fhsz": _fhsz,
    "wildfire_history": _wf_history,
    "wildfire_ml": _wf_ml,
    "earthquake_pga": _pga,
    "flood_nfhl": _flood,
    "tsunami": _binary,
    "coastal_flooding": _nri,
    "landslide": _landslide,
    "liquefaction": _binary,
    "windstorm": _nri,
    "drought": _nri,
    "extreme_heat": _nri,
    "winter_storm": _nri,
    "extreme_rainfall": _rain,
}


@dataclass
class CompositeScore:
    score: float | None
    label: str
    contributions: list[dict[str, Any]]
    missing_hazards: list[str]
    weights_note: str = WEIGHTS_NOTE
    method_version: str = METHOD_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "label": self.label,
            "contributions": self.contributions,
            "missing_hazards": self.missing_hazards,
            "weights_note": self.weights_note,
            "method_version": self.method_version,
        }


def score_label(score: float | None) -> str:
    if score is None:
        return "Insufficient coverage"
    if score >= 70:
        return "Very high screening priority"
    if score >= 50:
        return "High screening priority"
    if score >= 30:
        return "Moderate screening priority"
    return "Low screening priority"


def compute_composite(results: dict[str, HazardResult]) -> CompositeScore:
    """``results`` maps hazard_id → HazardResult (one per hazard)."""
    contributions: list[dict[str, Any]] = []
    missing: list[str] = []
    family_scores: dict[str, float] = {}
    for family, hazard_ids in FAMILIES.items():
        vals: list[tuple[str, float]] = []
        for hid in hazard_ids:
            r = results.get(hid)
            norm = NORMALISERS.get(hid)
            if r is None or norm is None:
                if hid != "wildfire_ml":
                    missing.append(hid)
                continue
            v = norm(r)
            if v is None:
                missing.append(hid)
                continue
            vals.append((hid, v))
            contributions.append(
                {
                    "hazard_id": hid,
                    "family": family,
                    "normalised": round(v, 3),
                    "raw_value": r.value,
                    "classification": r.classification,
                    "coverage_status": r.coverage_status,
                }
            )
        if not vals:
            continue
        rule = FAMILY_RULE[family]
        fs = max(v for _, v in vals) if rule == "max" else sum(v for _, v in vals) / len(vals)
        family_scores[family] = fs
    total_weight = sum(FAMILY_WEIGHTS[f] for f in family_scores)
    if total_weight < 0.5 * sum(FAMILY_WEIGHTS.values()):
        return CompositeScore(None, score_label(None), contributions, sorted(set(missing)))
    score = 100.0 * sum(FAMILY_WEIGHTS[f] * s for f, s in family_scores.items()) / total_weight
    for c in contributions:
        fam = c["family"]
        c["family_weight"] = FAMILY_WEIGHTS[fam]
        c["family_score"] = round(family_scores[fam], 3)
        c["weighted_points"] = round(
            100.0 * FAMILY_WEIGHTS[fam] * family_scores[fam] / total_weight, 1
        )
    return CompositeScore(round(score, 1), score_label(score), contributions, sorted(set(missing)))

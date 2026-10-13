"""Illustrative event-loss scenarios.

    gross physical loss = exposed value × scenario damage ratio
    net insured loss    = min(max(gross − deductible, 0), limit)   (only when terms are given)

Damage ratios are *assumptions* unless marked ``externally_derived`` with a citation.
Expected annual loss is **not** computed: CATRIX has no property-level event frequencies
or vulnerability curves, and heuristic scores are never used as probabilities.

Exposed value precedence: user-supplied insured value (observed) → user-supplied property
value (observed) → nothing. Area-level ACS medians are reported as context but are *not*
used as the exposed value without the user opting in, because they are not property values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from catrix.hazards.base import HazardResult

Kind = Literal["observed", "externally_derived", "assumed"]

ASSUMPTIONS_VERSION = "fs-1.0"


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    hazard_id: str
    description: str
    damage_ratio: float
    basis: str
    kind: Kind
    applies: Any  # Callable[[HazardResult | None], bool]


def _fhsz_at_least(level: float):
    return lambda r: r is not None and r.coverage_status == "covered" and (r.value or 0) >= level


def _sfha(r: HazardResult | None) -> bool:
    return r is not None and r.coverage_status == "covered" and r.value == 3.0


def _pga_at_least(g: float):
    return lambda r: r is not None and r.coverage_status == "covered" and (r.value or 0) >= g


# Damage ratios: FEMA Hazus provides published fragility/damage relationships for
# earthquake (by building type and PGA) and flood (depth-damage). Without building type,
# depth or construction data CATRIX cannot apply those curves, so the ratios below are
# simplified screening assumptions whose provenance is stated in `basis`.
SCENARIOS: list[ScenarioDefinition] = [
    ScenarioDefinition(
        "wildfire_vhfhsz_structure_loss",
        "wildfire_fhsz",
        "Wildfire reaches the property (Very High FHSZ): structure-loss scenario",
        0.80,
        "Assumed. Post-fire damage inspections (CAL FIRE DINS) show structures inside major "
        "WUI fire perimeters are frequently destroyed (>50% damage); 0.8 is a conservative "
        "screening assumption for a direct-impact scenario, not an expected value.",
        "assumed",
        _fhsz_at_least(3.0),
    ),
    ScenarioDefinition(
        "wildfire_hfhsz_partial_loss",
        "wildfire_fhsz",
        "Wildfire reaches the property (High FHSZ): partial-loss scenario",
        0.40,
        "Assumed screening ratio for ember/partial-exposure outcomes.",
        "assumed",
        lambda r: r is not None and r.coverage_status == "covered" and r.value == 2.0,
    ),
    ScenarioDefinition(
        "flood_sfha_1pct_event",
        "flood_nfhl",
        "1%-annual-chance flood at a Special Flood Hazard Area location (no depth data)",
        0.25,
        "Externally derived (simplified): FEMA Hazus residential depth-damage functions give "
        "roughly 20-30% building damage at ~1 m depth; without BFE/first-floor elevation a "
        "single midpoint ratio is used.",
        "externally_derived",
        _sfha,
    ),
    ScenarioDefinition(
        "earthquake_pga_0_4g_shaking",
        "earthquake_pga",
        "Shaking at PGA >= 0.4 g (strong) without site/building data",
        0.15,
        "Externally derived (simplified): Hazus wood-frame (W1) fragility implies mean damage "
        "ratios on the order of 10-20% at 0.4 g; building type is unknown so the midpoint is used.",
        "externally_derived",
        _pga_at_least(0.4),
    ),
    ScenarioDefinition(
        "earthquake_pga_0_6g_shaking",
        "earthquake_pga",
        "Shaking at PGA >= 0.6 g (very strong) without site/building data",
        0.30,
        "Externally derived (simplified) from Hazus W1 fragility midpoint at ~0.6 g.",
        "externally_derived",
        _pga_at_least(0.6),
    ),
]


@dataclass
class Assumption:
    name: str
    value: float | str
    kind: Kind
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "kind": self.kind, "source": self.source}


@dataclass
class ScenarioLoss:
    scenario_id: str
    hazard_id: str
    description: str
    damage_ratio: float
    exposed_value: float | None
    gross_loss: float | None
    deductible_applied: float | None
    limit_applied: float | None
    net_insured_loss: float | None
    assumptions: list[Assumption]
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["assumptions"] = [a.to_dict() for a in self.assumptions]
        return d


def apply_policy_terms(gross: float, deductible: float | None, limit: float | None) -> float:
    net = gross
    if deductible is not None:
        net = max(net - deductible, 0.0)
    if limit is not None:
        net = min(net, limit)
    return net


def resolve_exposed_value(
    insured_value: float | None, property_value: float | None
) -> tuple[float | None, Literal["observed", "externally_derived", "assumed", "missing"], str]:
    if insured_value is not None and insured_value > 0:
        return float(insured_value), "observed", "user-supplied insured value"
    if property_value is not None and property_value > 0:
        return (
            float(property_value),
            "observed",
            "user-supplied property value (used as proxy for exposed value)",
        )
    return None, "missing", "no value supplied"


def compute_scenarios(
    hazards: dict[str, HazardResult],
    *,
    insured_value: float | None = None,
    property_value: float | None = None,
    deductible: float | None = None,
    limit: float | None = None,
) -> dict[str, Any]:
    exposed, kind, basis = resolve_exposed_value(insured_value, property_value)
    out: list[ScenarioLoss] = []
    for s in SCENARIOS:
        if not s.applies(hazards.get(s.hazard_id)):
            continue
        assumptions = [
            Assumption("damage_ratio", s.damage_ratio, s.kind, s.basis),
            Assumption(
                "exposed_value",
                exposed if exposed is not None else "missing",
                kind if kind != "missing" else "assumed",
                basis,
            ),
        ]
        gross = exposed * s.damage_ratio if exposed is not None else None
        net = None
        if gross is not None and (deductible is not None or limit is not None):
            net = apply_policy_terms(gross, deductible, limit)
            if deductible is not None:
                assumptions.append(
                    Assumption("deductible", deductible, "observed", "user-supplied")
                )
            if limit is not None:
                assumptions.append(Assumption("limit", limit, "observed", "user-supplied"))
        caveats = [
            "Scenario loss for an assumed event, not an expected annual loss.",
            "Vulnerability is not building-specific (construction, year built and storeys are unknown).",
        ]
        if net is None and gross is not None:
            caveats.append("No policy terms supplied: net insured loss not computed.")
        out.append(
            ScenarioLoss(
                s.scenario_id,
                s.hazard_id,
                s.description,
                s.damage_ratio,
                exposed,
                None if gross is None else round(gross, 2),
                deductible if net is not None else None,
                limit if net is not None else None,
                None if net is None else round(net, 2),
                assumptions,
                caveats,
            )
        )
    return {
        "exposed_value": exposed,
        "exposed_value_kind": kind,
        "scenarios": [s.to_dict() for s in out],
        "expected_annual_loss_available": False,
        "note": "Scenario losses only. Expected annual loss is not computed because property-level event frequencies and vulnerability curves are not available; hazard screening scores are never used as probabilities.",
        "assumptions_version": ASSUMPTIONS_VERSION,
    }

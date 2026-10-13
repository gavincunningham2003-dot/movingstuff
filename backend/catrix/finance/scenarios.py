"""Illustrative event-loss scenarios.

    gross physical loss = exposed value × damage ratio
    net insured loss    = min(max(gross − deductible, 0), limit)   (only when terms are given)

Damage ratios come from two tiers:

* **Building-aware (preferred)** — when NSI structure attributes are matched, earthquake
  damage uses Hazus equivalent-PGA fragility curves for the mapped building type and
  design level, and flood damage uses Hazus/FIA depth-damage functions with depth derived
  from the NFHL base flood elevation (or assumed depths when no BFE exists). These are
  ``externally_derived`` with a verify flag (see ``hazus_tables.yaml``).
* **Flat screening ratios (fallback)** — when no structure is matched, the simplified
  ratios below apply and are labelled ``assumed`` / ``externally_derived (simplified)``.

Expected annual loss is **not** computed: CATRIX has no property-level event frequencies,
and heuristic scores are never used as probabilities.

Exposed value precedence: user insured value → user property value → NSI depreciated
structure value (``externally_derived``) → missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from catrix.finance.vulnerability import (
    HAZUS_SOURCE,
    BuildingProfile,
    assumed_flood_depths,
    earthquake_damage,
    flood_damage_ratio,
    flood_depth_ft,
    hazus_building_type,
)
from catrix.hazards.base import HazardResult

Kind = Literal["observed", "externally_derived", "assumed"]

ASSUMPTIONS_VERSION = "fs-2.0"


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


# Flat fallback scenarios (used when no building attributes are available).
FLAT_SCENARIOS: list[ScenarioDefinition] = [
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
        "1%-annual-chance flood at a Special Flood Hazard Area location (no depth or building data)",
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

# Kept for backwards compatibility with earlier imports/tests.
SCENARIOS = FLAT_SCENARIOS


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
    insured_value: float | None,
    property_value: float | None,
    structure_value: float | None = None,
) -> tuple[float | None, Literal["observed", "externally_derived", "assumed", "missing"], str]:
    if insured_value is not None and insured_value > 0:
        return float(insured_value), "observed", "user-supplied insured value"
    if property_value is not None and property_value > 0:
        return (
            float(property_value),
            "observed",
            "user-supplied property value (used as proxy for exposed value)",
        )
    if structure_value is not None and structure_value > 0:
        return (
            float(structure_value),
            "externally_derived",
            "NSI depreciated structure replacement value (modelled, not surveyed)",
        )
    return None, "missing", "no value supplied"


def _finish(
    scenario_id: str,
    hazard_id: str,
    description: str,
    ratio: float,
    exposed: float | None,
    exposed_assumption: Assumption,
    extra_assumptions: list[Assumption],
    deductible: float | None,
    limit: float | None,
    caveats: list[str],
) -> ScenarioLoss:
    gross = exposed * ratio if exposed is not None else None
    net = None
    assumptions = [*extra_assumptions, exposed_assumption]
    if gross is not None and (deductible is not None or limit is not None):
        net = apply_policy_terms(gross, deductible, limit)
        if deductible is not None:
            assumptions.append(Assumption("deductible", deductible, "observed", "user-supplied"))
        if limit is not None:
            assumptions.append(Assumption("limit", limit, "observed", "user-supplied"))
    cav = [
        "Scenario loss for an assumed event, not an expected annual loss.",
        *caveats,
    ]
    if net is None and gross is not None:
        cav.append("No policy terms supplied: net insured loss not computed.")
    return ScenarioLoss(
        scenario_id,
        hazard_id,
        description,
        round(ratio, 4),
        exposed,
        None if gross is None else round(gross, 2),
        deductible if net is not None else None,
        limit if net is not None else None,
        None if net is None else round(net, 2),
        assumptions,
        cav,
    )


def compute_scenarios(
    hazards: dict[str, HazardResult],
    *,
    building: BuildingProfile | None = None,
    insured_value: float | None = None,
    property_value: float | None = None,
    deductible: float | None = None,
    limit: float | None = None,
) -> dict[str, Any]:
    exposed, kind, basis = resolve_exposed_value(
        insured_value, property_value, building.val_struct_usd if building else None
    )
    exposed_assumption = Assumption(
        "exposed_value",
        exposed if exposed is not None else "missing",
        kind if kind != "missing" else "assumed",
        basis,
    )
    out: list[ScenarioLoss] = []
    building_caveat = (
        "Vulnerability is not building-specific (construction, year built and storeys are unknown)."
        if building is None
        else "Building attributes are NSI modelled estimates (not surveyed); Hazus tables are transcribed and must be verified."
    )

    # Wildfire: no Hazus wildfire fragility exists; flat screening ratios apply either way.
    for s in FLAT_SCENARIOS:
        if s.hazard_id != "wildfire_fhsz" or not s.applies(hazards.get(s.hazard_id)):
            continue
        extra = [Assumption("damage_ratio", s.damage_ratio, s.kind, s.basis)]
        if building is not None:
            extra.append(
                Assumption(
                    "building_type",
                    f"{building.bldgtype or '?'} / {hazus_building_type(building)}",
                    "externally_derived",
                    "NSI",
                )
            )
        out.append(
            _finish(
                s.scenario_id,
                s.hazard_id,
                s.description,
                s.damage_ratio,
                exposed,
                exposed_assumption,
                extra,
                deductible,
                limit,
                [
                    "No building-specific wildfire vulnerability model is available; ratio is a screening assumption.",
                    building_caveat,
                ],
            )
        )

    # Earthquake -------------------------------------------------------------------
    for hid, label in (
        ("earthquake_pga", "2% in 50 years (2,475-yr)"),
        ("earthquake_pga_10pc50", "10% in 50 years (475-yr)"),
    ):
        eq = hazards.get(hid)
        if eq is None or eq.coverage_status != "covered" or eq.value is None:
            continue
        if building is not None:
            dmg = earthquake_damage(eq.value, building)
            extra = [
                Assumption(a["name"], a["value"], a["kind"], a["source"])
                for a in dmg.to_assumptions()
            ]
            extra.insert(
                0,
                Assumption(
                    "damage_ratio",
                    round(dmg.mean_damage_ratio, 4),
                    "externally_derived",
                    HAZUS_SOURCE,
                ),
            )
            out.append(
                _finish(
                    f"{hid}_hazus_fragility",
                    hid,
                    f"Shaking at the {label} PGA ({eq.value:.2f} g): Hazus fragility for {dmg.hazus_type} ({dmg.design_level}-code)",
                    dmg.mean_damage_ratio,
                    exposed,
                    exposed_assumption,
                    extra,
                    deductible,
                    limit,
                    [
                        "Damage-state probabilities: "
                        + ", ".join(f"{k} {v:.2f}" for k, v in dmg.p_state.items())
                        + ".",
                        "PGA is at reference site conditions (no Vs30 amplification).",
                        building_caveat,
                    ],
                )
            )
        elif hid == "earthquake_pga":
            for s in FLAT_SCENARIOS:
                if s.hazard_id == "earthquake_pga" and s.applies(eq):
                    out.append(
                        _finish(
                            s.scenario_id,
                            s.hazard_id,
                            s.description,
                            s.damage_ratio,
                            exposed,
                            exposed_assumption,
                            [Assumption("damage_ratio", s.damage_ratio, s.kind, s.basis)],
                            deductible,
                            limit,
                            [building_caveat],
                        )
                    )

    # Flood ---------------------------------------------------------------------------
    fl = hazards.get("flood_nfhl")
    if _sfha(fl):
        assert fl is not None
        if building is not None:
            bfe = (fl.details or {}).get("static_bfe_ft")
            depth = flood_depth_ft(bfe, building)
            depths = (
                [("bfe", depth)]
                if depth is not None
                else [("assumed", d) for d in assumed_flood_depths()]
            )
            for how, d in depths:
                ratio, curve = flood_damage_ratio(d, building)
                extra = [
                    Assumption("damage_ratio", round(ratio, 4), "externally_derived", HAZUS_SOURCE),
                    Assumption(
                        "depth_above_first_floor_ft",
                        round(d, 2),
                        "externally_derived" if how == "bfe" else "assumed",
                        "NFHL STATIC_BFE minus (NSI ground elevation + foundation height)"
                        if how == "bfe"
                        else "assumed scenario depth (no BFE at this location)",
                    ),
                    Assumption("depth_damage_curve", curve, "externally_derived", HAZUS_SOURCE),
                    Assumption("storeys", building.num_story or 1, "externally_derived", "NSI"),
                    Assumption(
                        "foundation", building.found_type or "?", "externally_derived", "NSI"
                    ),
                ]
                sid = "flood_sfha_bfe_depth_hazus" if how == "bfe" else f"flood_sfha_{d:g}ft_hazus"
                desc = (
                    f"1%-annual-chance flood at BFE: depth {d:.1f} ft above first floor, Hazus depth-damage ({curve})"
                    if how == "bfe"
                    else f"1%-annual-chance flood, assumed {d:g} ft above first floor, Hazus depth-damage ({curve})"
                )
                out.append(
                    _finish(
                        sid,
                        "flood_nfhl",
                        desc,
                        ratio,
                        exposed,
                        exposed_assumption,
                        extra,
                        deductible,
                        limit,
                        [
                            "Depth is an estimate; ground and first-floor elevations are modelled, and pluvial flooding is excluded.",
                            building_caveat,
                        ],
                    )
                )
        else:
            s = next(x for x in FLAT_SCENARIOS if x.scenario_id == "flood_sfha_1pct_event")
            out.append(
                _finish(
                    s.scenario_id,
                    s.hazard_id,
                    s.description,
                    s.damage_ratio,
                    exposed,
                    exposed_assumption,
                    [Assumption("damage_ratio", s.damage_ratio, s.kind, s.basis)],
                    deductible,
                    limit,
                    [building_caveat],
                )
            )

    return {
        "exposed_value": exposed,
        "exposed_value_kind": kind,
        "scenarios": [s.to_dict() for s in out],
        "expected_annual_loss_available": False,
        "building_profile": (
            {
                "bldgtype": building.bldgtype,
                "hazus_type": hazus_building_type(building),
                "occtype": building.occtype,
                "num_story": building.num_story,
                "year_built": building.year_built,
                "found_type": building.found_type,
                "found_ht_ft": building.found_ht_ft,
                "sqft": building.sqft,
                "val_struct_usd": building.val_struct_usd,
            }
            if building is not None
            else None
        ),
        "note": "Scenario losses only. Expected annual loss is not computed because property-level event frequencies are not available; hazard screening scores are never used as probabilities. "
        + (
            "Building-aware Hazus damage functions were applied."
            if building is not None
            else "No matched structure: flat screening damage ratios were applied."
        ),
        "assumptions_version": ASSUMPTIONS_VERSION,
    }

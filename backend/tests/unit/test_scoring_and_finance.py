from catrix.finance.scenarios import (
    SENSITIVITY_MULTIPLIERS,
    apply_policy_terms,
    compute_scenarios,
    exposed_value_basis,
    resolve_exposed_value,
    resolve_limit,
)
from catrix.hazards.base import HazardResult
from catrix.scoring.composite import FAMILY_WEIGHTS, compute_composite


def _r(hid, status="covered", value=None, cls=None, details=None):
    return HazardResult(
        hid, hid, "src", status, value=value, classification=cls, details=details or {}
    )


def test_composite_uses_family_max_and_reports_missing():
    res = {
        "wildfire_fhsz": _r("wildfire_fhsz", value=3.0, cls="Very High"),
        "wildfire_history": _r(
            "wildfire_history", value=2.0, details={"inside_any_perimeter": False}
        ),
        "earthquake_pga": _r("earthquake_pga", value=0.5),
        "flood_nfhl": _r("flood_nfhl", value=1.0),
        "landslide": _r("landslide", status="unavailable"),
    }
    c = compute_composite(res)
    assert c.score is not None and 0 <= c.score <= 100
    assert "landslide" in c.missing_hazards and "liquefaction" in c.missing_hazards
    fam = {x["hazard_id"]: x for x in c.contributions}
    assert fam["wildfire_fhsz"]["family_score"] == 1.0  # max of (1.0, 0.16)
    assert "assumption" in c.weights_note.lower()
    # wildfire family at 1.0 with weight .35, eq .5*.30, flood .05*.20 over total weight .85
    expected = 100 * (0.35 * 1.0 + 0.30 * 0.5 + 0.20 * 0.05) / (0.35 + 0.30 + 0.20)
    assert abs(c.score - round(expected, 1)) < 0.11


def test_composite_withheld_when_coverage_insufficient():
    c = compute_composite({"windstorm": _r("windstorm", value=50.0)})
    assert c.score is None and c.label == "Insufficient coverage"


def test_unzoned_fhsz_counts_as_zero_but_unmapped_flood_counts_as_missing():
    res = {
        "wildfire_fhsz": _r("wildfire_fhsz", status="not_in_hazard_area"),
        "earthquake_pga": _r("earthquake_pga", value=0.2),
        "flood_nfhl": _r("flood_nfhl", status="outside_coverage"),
    }
    c = compute_composite(res)
    assert "flood_nfhl" in c.missing_hazards and "wildfire_fhsz" not in c.missing_hazards
    assert sum(FAMILY_WEIGHTS.values()) == 1.0


def test_policy_terms():
    assert apply_policy_terms(100.0, 10.0, None) == 90.0
    assert apply_policy_terms(100.0, None, 50.0) == 50.0
    assert apply_policy_terms(5.0, 10.0, 50.0) == 0.0


def test_exposed_value_precedence_prefers_replacement_values_over_sum_insured():
    # replacement value (building + contents) is the physical value at risk
    assert resolve_exposed_value(500.0, 900.0)[0:2] == (900.0, "observed")
    assert resolve_exposed_value(500.0, 900.0, contents_value=100.0)[0] == 1000.0
    assert exposed_value_basis(500.0, 900.0) == "replacement_value"
    # the sum insured is only a proxy when no replacement value is known
    assert resolve_exposed_value(500.0, None)[0:2] == (500.0, "observed")
    assert exposed_value_basis(500.0, None) == "sum_insured_proxy"
    assert exposed_value_basis(None, None, 250.0) == "nsi_structure"
    assert resolve_exposed_value(None, None)[1] == "missing"


def test_limit_defaults_to_sum_insured():
    assert resolve_limit(300.0, 500.0) == (300.0, "policy_limit")
    assert resolve_limit(None, 500.0) == (500.0, "sum_insured")
    assert resolve_limit(None, None) == (None, "none")


def test_net_loss_is_capped_by_sum_insured_and_gross_uses_replacement_value():
    res = {"wildfire_fhsz": _r("wildfire_fhsz", value=3.0, cls="Very High")}
    out = compute_scenarios(res, property_value=1_000_000, insured_value=600_000, deductible=10_000)
    wf = out["scenarios"][0]
    assert out["exposed_value"] == 1_000_000 and out["exposed_value_basis"] == "replacement_value"
    assert out["limit"] == 600_000 and out["limit_basis"] == "sum_insured"
    assert wf["gross_loss"] == 800_000 and wf["net_insured_loss"] == 600_000
    assert any(
        a["name"] == "limit" and "sum insured" in (a["source"] or "") for a in wf["assumptions"]
    )
    # an explicit policy limit wins over the sum insured
    out2 = compute_scenarios(res, property_value=1_000_000, insured_value=600_000, limit=400_000)
    assert out2["limit_basis"] == "policy_limit"
    assert out2["scenarios"][0]["net_insured_loss"] == 400_000
    # contents are added to the building value, never the sum insured
    out3 = compute_scenarios(
        res, property_value=1_000_000, contents_value=200_000, insured_value=5_000_000
    )
    assert out3["exposed_value"] == 1_200_000 and out3["building_value"] == 1_000_000
    assert out3["contents_value"] == 200_000 and out3["sum_insured"] == 5_000_000


def test_sensitivity_rows_scale_the_damage_ratio_and_cap_at_one():
    res = {"wildfire_fhsz": _r("wildfire_fhsz", value=3.0, cls="Very High")}
    out = compute_scenarios(res, property_value=100.0, insured_value=70.0, deductible=5.0)
    wf = out["scenarios"][0]
    assert out["sensitivity_multipliers"] == list(SENSITIVITY_MULTIPLIERS)
    sens = {s["multiplier"]: s for s in wf["sensitivity"]}
    assert sens[1.0]["gross_loss"] == wf["gross_loss"]
    assert sens[0.5]["gross_loss"] == 40.0 and sens[0.5]["net_insured_loss"] == 35.0
    assert sens[1.5]["damage_ratio"] == 1.0 and sens[1.5]["gross_loss"] == 100.0
    assert sens[1.5]["net_insured_loss"] == 70.0  # capped by the sum insured


def test_scenarios_apply_only_where_hazard_triggers_and_terms_given():
    res = {
        "wildfire_fhsz": _r("wildfire_fhsz", value=3.0, cls="Very High"),
        "flood_nfhl": _r("flood_nfhl", value=1.0),
        "earthquake_pga": _r("earthquake_pga", value=0.65),
    }
    out = compute_scenarios(res, insured_value=1_000_000, deductible=10_000, limit=500_000)
    ids = {s["scenario_id"] for s in out["scenarios"]}
    assert "wildfire_vhfhsz_structure_loss" in ids and "flood_sfha_1pct_event" not in ids
    assert {"earthquake_pga_0_4g_shaking", "earthquake_pga_0_6g_shaking"} <= ids
    wf = next(s for s in out["scenarios"] if s["scenario_id"] == "wildfire_vhfhsz_structure_loss")
    assert wf["gross_loss"] == 800_000 and wf["net_insured_loss"] == 500_000
    assert out["expected_annual_loss_available"] is False
    kinds = {a["kind"] for s in out["scenarios"] for a in s["assumptions"]}
    assert "observed" in kinds and ("assumed" in kinds or "externally_derived" in kinds)
    no_terms = compute_scenarios(res, property_value=100.0)
    assert all(s["net_insured_loss"] is None for s in no_terms["scenarios"])
    nothing = compute_scenarios(res)
    assert (
        all(s["gross_loss"] is None for s in nothing["scenarios"])
        and nothing["exposed_value_kind"] == "missing"
    )

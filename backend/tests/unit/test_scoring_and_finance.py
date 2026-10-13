from catrix.finance.scenarios import apply_policy_terms, compute_scenarios, resolve_exposed_value
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


def test_exposed_value_precedence():
    assert resolve_exposed_value(500.0, 900.0)[0:2] == (500.0, "observed")
    assert resolve_exposed_value(None, 900.0)[0:2] == (900.0, "observed")
    assert resolve_exposed_value(None, None)[1] == "missing"


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

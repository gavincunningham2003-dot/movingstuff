"""Pure parts of the historical stress test (no database)."""

from __future__ import annotations

import pytest

from catrix.portfolio.stress import (
    ASSUMPTIONS,
    DEFAULT_DAMAGE_RATIO,
    StressHit,
    StressTestResult,
    hit_loss,
    hits_to_rows,
)


def test_hit_loss_uses_replacement_value_and_caps_net_by_sum_insured():
    exposed, basis, lim, gross, net = hit_loss(
        damage_ratio=0.5,
        insured_value=600.0,
        property_value=1000.0,
        contents_value=200.0,
        deductible=50.0,
        limit=None,
    )
    assert exposed == 1200.0 and basis == "replacement_value" and lim == 600.0
    assert gross == 600.0 and net == 550.0


def test_hit_loss_without_terms_or_value():
    exposed, basis, lim, gross, net = hit_loss(
        damage_ratio=0.5,
        insured_value=None,
        property_value=400.0,
        contents_value=None,
        deductible=None,
        limit=None,
    )
    assert (exposed, basis, lim, gross, net) == (400.0, "replacement_value", None, 200.0, None)
    assert hit_loss(
        damage_ratio=0.5,
        insured_value=None,
        property_value=None,
        contents_value=None,
        deductible=None,
        limit=None,
    ) == (None, "missing", None, None, None)


def test_sum_insured_is_only_a_proxy_and_explicit_limit_wins():
    exposed, basis, lim, gross, net = hit_loss(
        damage_ratio=1.0,
        insured_value=500.0,
        property_value=None,
        contents_value=None,
        deductible=None,
        limit=300.0,
    )
    assert exposed == 500.0 and basis == "sum_insured_proxy"
    assert lim == 300.0 and gross == 500.0 and net == 300.0


def test_export_rows_carry_event_and_ratio():
    hit = StressHit(
        property_id="A",
        policy_id="P",
        row_number=1,
        lat=37.0,
        lon=-122.0,
        county_fips="06001",
        hit_kind="inside",
        distance_m=0.0,
        insured_value=100.0,
        property_value=None,
        contents_value=None,
        exposed_value=100.0,
        exposed_value_basis="sum_insured_proxy",
        deductible=None,
        limit=100.0,
        gross_loss=50.0,
        net_insured_loss=50.0,
        composite_score=None,
        duplicate_kind=None,
    )
    res = StressTestResult(
        portfolio_id="pf",
        event={"event_id": 7, "fire_name": "CAMP", "year": 2018},
        damage_ratio=DEFAULT_DAMAGE_RATIO,
        buffer_m=0.0,
        generated_at="2026-10-14T00:00:00+00:00",
        totals={},
        hits=[hit],
        perimeter=None,
        assumptions=list(ASSUMPTIONS),
    )
    rows = hits_to_rows(res)
    assert rows[0]["fire_name"] == "CAMP" and rows[0]["fire_year"] == 2018
    assert rows[0]["damage_ratio"] == DEFAULT_DAMAGE_RATIO and rows[0]["gross_loss"] == 50.0
    assert any("not structure loss" in a for a in res.assumptions)


def test_damage_ratio_must_be_in_range():
    from unittest.mock import MagicMock

    from catrix.portfolio.stress import run_stress_test

    with pytest.raises(ValueError):
        run_stress_test(MagicMock(), "pf", 1, damage_ratio=0.0)
    with pytest.raises(ValueError):
        run_stress_test(MagicMock(), "pf", 1, damage_ratio=1.5)

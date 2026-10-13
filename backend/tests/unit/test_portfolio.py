from pathlib import Path

import pandas as pd
import pytest

from catrix.portfolio.analytics import summarise_portfolio
from catrix.portfolio.validation import map_columns, parse_portfolio_csv

EXAMPLE = Path(__file__).resolve().parents[3] / "data" / "samples" / "example_portfolio.csv"


def test_map_columns_accepts_aliases():
    m = map_columns(["ID", "Lat", "Lng", "TIV", "Policy"])
    assert m == {
        "property_id": "ID",
        "latitude": "Lat",
        "longitude": "Lng",
        "insured_value": "TIV",
        "policy_id": "Policy",
    }


def test_parse_example_portfolio_flags_duplicates_and_invalid_rows():
    df, rep = parse_portfolio_csv(EXAMPLE.read_bytes())
    assert rep.n_rows == 22 and rep.n_invalid == 1 and rep.n_duplicates == 1
    assert any(e["row"] == 22 for e in rep.errors)
    dup = df[df["is_duplicate_of"].notna()]
    assert dup["property_id"].tolist() == ["P-0021"] and dup["is_duplicate_of"].iloc[0] == 3
    assert df.loc[0, "insured_value"] == 700000 and df.loc[0, "deductible"] == 5000


def test_parse_rejects_missing_required_columns():
    with pytest.raises(ValueError, match="missing required columns"):
        parse_portfolio_csv(b"foo,bar\n1,2\n")
    with pytest.raises(ValueError, match="no data rows"):
        parse_portfolio_csv(b"property_id,latitude,longitude\n")


def _hz(fhsz=None, sfha=False, pga=None):
    h = {}
    h["wildfire_fhsz"] = (
        {"coverage_status": "covered", "classification": fhsz}
        if fhsz
        else {"coverage_status": "not_in_hazard_area"}
    )
    h["flood_nfhl"] = (
        {
            "coverage_status": "covered",
            "classification": "SFHA",
            "value": 3.0,
            "details": {"sfha": True},
        }
        if sfha
        else {"coverage_status": "outside_coverage"}
    )
    h["earthquake_pga"] = (
        {"coverage_status": "covered", "value": pga}
        if pga is not None
        else {"coverage_status": "unavailable"}
    )
    return h


def test_summarise_portfolio_counts_hazards_and_money_separately():
    df = pd.DataFrame(
        {
            "property_id": ["a", "b", "c", "d"],
            "lat": [37.87, 37.87, 34.05, 38.75],
            "lon": [-122.27, -122.27, -118.25, -120.65],
            "county_fips": ["06001", "06001", "06037", "06017"],
            "insured_value": [100.0, 100.0, None, 300.0],
            "property_value": [None, None, 50.0, None],
            "is_duplicate_of": [None, 1, None, None],
            "composite_score": [80.0, 80.0, None, 20.0],
            "hazards": [
                _hz("Very High", True, 0.7),
                _hz("Very High", True, 0.7),
                _hz(None, False, 0.3),
                _hz("High", False, None),
            ],
            "financial": [
                {
                    "scenarios": [
                        {
                            "scenario_id": "s1",
                            "hazard_id": "wildfire_fhsz",
                            "description": "x",
                            "damage_ratio": 0.8,
                            "exposed_value": 100.0,
                            "gross_loss": 80.0,
                            "net_insured_loss": 70.0,
                        }
                    ]
                },
                None,
                {"scenarios": []},
                {"scenarios": []},
            ],
        }
    )
    out = summarise_portfolio(
        df, {"06001": "Alameda", "06037": "Los Angeles", "06017": "El Dorado"}
    )
    s = out["summary"]
    assert s["n_properties"] == 3 and s["n_duplicates_excluded"] == 1
    assert s["n_fhsz_very_high"] == 1 and s["n_sfha"] == 1 and s["n_pga_ge_0_5g"] == 1
    assert out["financial"]["total_insured_value"] == 400.0
    sc = out["financial"]["scenarios"][0]
    assert sc["gross_loss"] == 80.0 and sc["net_insured_loss"] == 70.0 and sc["n_properties"] == 1
    assert out["hazard_distribution"]["wildfire_fhsz"] == {
        "Very High": 1,
        "High": 1,
        "not in hazard area": 1,
    }
    assert out["coverage"]["earthquake_pga"] == {"covered": 2, "unavailable": 1}
    assert out["by_county"][0]["county"] in ("Alameda", "Los Angeles", "El Dorado")
    assert (
        out["top_exposures"][0]["property_id"] == "a"
        and "FHSZ Very High" in out["top_exposures"][0]["drivers"]
    )
    assert out["concentration"] and out["concentration"][0]["share_of_tiv_pct"] is not None
    assert any("duplicate" in w for w in out["warnings"])

from pathlib import Path

import pandas as pd
import pytest

from catrix.portfolio.analytics import summarise_portfolio
from catrix.portfolio.validation import CSV_CONTRACT, map_columns, parse_portfolio_csv

EXAMPLE = Path(__file__).resolve().parents[3] / "data" / "samples" / "example_portfolio.csv"


def test_map_columns_accepts_aliases():
    m = map_columns(["ID", "Lat", "Lng", "TIV", "Policy", "RCV", "Contents", "Policy Limit"])
    assert m == {
        "property_id": "ID",
        "latitude": "Lat",
        "longitude": "Lng",
        "insured_value": "TIV",
        "policy_id": "Policy",
        "property_value": "RCV",
        "contents_value": "Contents",
        "limit": "Policy Limit",
    }
    # the served contract is the same table the parser uses
    assert {c["name"] for c in CSV_CONTRACT} >= set(m)
    assert [c["name"] for c in CSV_CONTRACT if c["required"]] == ["latitude", "longitude"]


def test_parse_example_portfolio_classifies_duplicates_and_reports_field_problems():
    df, rep = parse_portfolio_csv(EXAMPLE.read_bytes())
    assert rep.n_rows == 25 and rep.n_invalid == 1
    # exact duplicate (same id, same location) is the only excluded kind
    assert rep.n_duplicates == 1 and rep.n_shared_location == 1 and rep.n_repeated_id == 0
    kinds = df.set_index("property_id")["duplicate_kind"]
    assert kinds["P-0021"] == "shared_location"
    assert df[df["duplicate_kind"] == "exact"]["property_id"].tolist() == ["P-0003"]
    assert df[df["duplicate_kind"] == "exact"]["is_duplicate_of"].iloc[0] == 3
    assert rep.n_locations == rep.n_valid - rep.n_duplicates - rep.n_shared_location
    # bad coordinates: row-level error; malformed amount: field-level warning, row kept
    coord = next(e for e in rep.errors if e["row"] == 23)
    assert coord["severity"] == "error" and coord["field"] == "latitude/longitude"
    bad_amt = next(e for e in rep.errors if e["row"] == 24)
    assert bad_amt["severity"] == "warning" and bad_amt["field"] == "insured_value"
    assert bad_amt["value"] == "n/a" and bool(df.loc[23, "valid"]) is True
    assert pd.isna(df.loc[23, "insured_value"]) and df.loc[23, "property_value"] == 1_250_000
    # empty amounts are simply missing, never errors
    assert pd.isna(df.loc[23, "deductible"]) and not any(
        e["row"] == 24 and e.get("field") == "deductible" for e in rep.errors
    )
    # missing property_id is generated from the row number
    assert df.loc[24, "property_id"] == "row-25" and rep.n_generated_ids == 1
    assert df.loc[0, "insured_value"] == 700000 and df.loc[0, "deductible"] == 5000
    assert df.loc[0, "contents_value"] == 212000 and df.loc[0, "limit"] == 700000


def test_parse_without_property_id_column_generates_ids():
    df, rep = parse_portfolio_csv(b"lat,lon,tiv\n37.87,-122.27,100\n37.80,-122.25,\n")
    assert df["property_id"].tolist() == ["row-1", "row-2"] and rep.n_generated_ids == 2
    assert rep.n_valid == 2 and pd.isna(df.loc[1, "insured_value"])


def test_duplicate_kinds():
    csv = (
        b"property_id,lat,lon,tiv\n"
        b"A,37.87,-122.27,100\n"
        b"A,37.87,-122.27,100\n"  # exact
        b"B,37.87,-122.27,50\n"  # shared location
        b"A,34.05,-118.25,10\n"  # repeated id elsewhere
    )
    df, rep = parse_portfolio_csv(csv)
    assert df["duplicate_kind"].tolist() == [None, "exact", "shared_location", "repeated_id"]
    assert df["is_duplicate_of"].tolist() == [None, 1, 1, 1]
    assert (rep.n_duplicates, rep.n_shared_location, rep.n_repeated_id, rep.n_locations) == (
        1,
        1,
        1,
        2,
    )


def test_negative_amount_invalidates_row_but_malformed_only_warns():
    df, rep = parse_portfolio_csv(b"id,lat,lon,tiv\nA,37.87,-122.27,-5\nB,37.80,-122.25,abc\n")
    assert df["valid"].tolist() == [False, True]
    sev = {e["row"]: e["severity"] for e in rep.errors}
    assert sev == {1: "error", 2: "warning"}


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
            "property_id": ["a", "a", "c", "d", "e"],
            "policy_id": ["P1", "P1", "P2", "P3", "P4"],
            "lat": [37.87, 37.87, 34.05, 38.75, 37.87],
            "lon": [-122.27, -122.27, -118.25, -120.65, -122.27],
            "county_fips": ["06001", "06001", "06037", "06017", "06001"],
            "insured_value": [100.0, 100.0, None, 300.0, 60.0],
            "property_value": [None, None, 50.0, None, 80.0],
            "contents_value": [None, None, None, None, 20.0],
            "policy_limit": [None, None, None, 250.0, None],
            "deductible": [10.0, 10.0, None, None, None],
            "is_duplicate_of": [None, 1, None, None, 1],
            "duplicate_kind": [None, "exact", None, None, "shared_location"],
            "location_key": ["L1", "L1", "L2", "L3", "L1"],
            "composite_score": [80.0, 80.0, None, 20.0, 75.0],
            "hazards": [
                _hz("Very High", True, 0.7),
                _hz("Very High", True, 0.7),
                _hz(None, False, 0.3),
                _hz("High", False, None),
                _hz("Very High", True, 0.7),
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
                            "sensitivity": [
                                {"multiplier": 0.5, "gross_loss": 40.0, "net_insured_loss": 30.0},
                                {"multiplier": 1.0, "gross_loss": 80.0, "net_insured_loss": 70.0},
                            ],
                        }
                    ],
                    "exposed_value_basis": "sum_insured_proxy",
                    "limit_basis": "sum_insured",
                },
                None,
                {"scenarios": []},
                {"scenarios": []},
                {
                    "scenarios": [
                        {
                            "scenario_id": "s1",
                            "hazard_id": "wildfire_fhsz",
                            "description": "x",
                            "damage_ratio": 0.8,
                            "exposed_value": 100.0,
                            "gross_loss": 80.0,
                            "net_insured_loss": 60.0,
                            "sensitivity": [
                                {"multiplier": 0.5, "gross_loss": 40.0, "net_insured_loss": 40.0},
                                {"multiplier": 1.0, "gross_loss": 80.0, "net_insured_loss": 60.0},
                            ],
                        }
                    ],
                    "exposed_value_basis": "replacement_value",
                    "limit_basis": "sum_insured",
                },
            ],
        }
    )
    out = summarise_portfolio(
        df, {"06001": "Alameda", "06037": "Los Angeles", "06017": "El Dorado"}
    )
    s = out["summary"]
    # the exact duplicate is excluded; the shared-location exposure is kept
    assert s["n_properties"] == 4 and s["n_exposures"] == 4 and s["n_duplicates_excluded"] == 1
    assert s["n_shared_location_exposures"] == 1 and s["n_locations"] == 3
    assert s["n_fhsz_very_high"] == 2 and s["n_sfha"] == 2 and s["n_pga_ge_0_5g"] == 2
    fin = out["financial"]
    # each kind of money is summed separately and never mixed
    assert fin["total_insured_value"] == 460.0 and fin["total_property_value"] == 130.0
    assert fin["total_contents_value"] == 20.0 and fin["total_replacement_value"] == 150.0
    assert fin["total_policy_limit"] == 250.0 and fin["n_with_limit"] == 1
    assert fin["n_with_deductible"] == 1 and fin["total_deductible"] == 10.0
    assert fin["exposed_value_basis_counts"] == {"sum_insured_proxy": 1, "replacement_value": 1}
    sc = fin["scenarios"][0]
    assert sc["gross_loss"] == 160.0 and sc["net_insured_loss"] == 130.0
    assert sc["n_properties"] == 2 and sc["n_with_terms"] == 2 and sc["n_without_terms"] == 0
    sens = {r["multiplier"]: r for r in sc["sensitivity"]}
    assert sens[0.5]["gross_loss"] == 80.0 and sens[0.5]["net_insured_loss"] == 70.0
    # accumulation at the shared location is reported, not hidden
    acc = out["location_accumulation"]
    assert len(acc) == 1 and acc[0]["n_exposures"] == 2 and acc[0]["total_insured_value"] == 160.0
    assert sorted(acc[0]["property_ids"]) == ["a", "e"] and acc[0]["policy_ids"] == ["P1", "P4"]
    assert acc[0]["share_of_tiv_pct"] == pytest.approx(100 * 160 / 460, abs=0.01)
    assert out["hazard_distribution"]["wildfire_fhsz"] == {
        "Very High": 2,
        "High": 1,
        "not in hazard area": 1,
    }
    assert out["coverage"]["earthquake_pga"] == {"covered": 3, "unavailable": 1}
    assert out["by_county"][0]["county"] in ("Alameda", "Los Angeles", "El Dorado")
    assert (
        out["top_exposures"][0]["property_id"] == "a"
        and "FHSZ Very High" in out["top_exposures"][0]["drivers"]
    )
    assert out["concentration"] and out["concentration"][0]["share_of_tiv_pct"] is not None
    assert any("duplicate" in w for w in out["warnings"])
    assert any("share a location" in w for w in out["warnings"])


def test_summarise_portfolio_accepts_legacy_rows_without_duplicate_kind():
    df = pd.DataFrame(
        {
            "property_id": ["a", "b"],
            "lat": [37.87, 37.87],
            "lon": [-122.27, -122.27],
            "county_fips": ["06001", "06001"],
            "insured_value": [100.0, 100.0],
            "property_value": [None, None],
            "is_duplicate_of": [None, 1],
            "composite_score": [80.0, 80.0],
            "hazards": [_hz("High", False, None), _hz("High", False, None)],
            "financial": [None, None],
        }
    )
    out = summarise_portfolio(df, {})
    # a legacy location duplicate is a shared-location exposure: kept, not excluded
    assert out["summary"]["n_properties"] == 2 and out["summary"]["n_duplicates_excluded"] == 0
    assert out["summary"]["n_shared_location_exposures"] == 1

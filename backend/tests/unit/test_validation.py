import pandas as pd

from catrix.geo.validation import check_coordinates, dedup_key, in_california_bbox


def test_check_coordinates_flags_each_failure_mode():
    df = pd.DataFrame(
        {
            "lat": [37.8, None, 95.0, 0.0, 40.7, 36.0],
            "lon": [-122.3, -122.0, -122.0, 0.0, -74.0, -119.5],
        }
    )
    c = check_coordinates(df)
    assert c.n_total == 6
    assert c.n_missing == 1
    assert c.n_impossible == 1
    assert c.n_zero == 1
    assert c.n_outside_california == 1  # New York
    assert c.valid_mask.tolist() == [True, False, False, False, False, True]


def test_check_coordinates_without_california_requirement():
    df = pd.DataFrame({"lat": [40.7], "lon": [-74.0]})
    assert check_coordinates(df, require_california=False).n_valid == 1


def test_in_california_bbox():
    assert in_california_bbox(-122.4, 37.8)
    assert not in_california_bbox(-74.0, 40.7)


def test_dedup_key_collapses_nearby_points_and_separates_distant_ones():
    a = dedup_key(37.87162, -122.2727)
    b = dedup_key(37.87162 + 0.000002, -122.2727)  # ~0.2 m, same ~1 m cell
    c = dedup_key(37.87162 + 0.001, -122.2727)  # ~110 m
    assert a == b
    assert a != c


def test_negative_financial_values_make_the_row_invalid():
    from catrix.portfolio.validation import parse_portfolio_csv

    csv = chr(10).join(
        [
            "property_id,latitude,longitude,insured_value,deductible",
            "A,37.5,-122.0,500000,1000",
            "B,37.6,-122.1,500000,-1",
            "C,37.7,-122.2,-5,0",
        ]
    )
    df, rep = parse_portfolio_csv(csv)
    assert rep.n_rows == 3 and rep.n_valid == 1 and rep.n_invalid == 2
    assert {e["error"] for e in rep.errors} == {"negative deductible", "negative insured_value"}
    assert df.loc[df["property_id"] == "A", "valid"].all()
    assert not df.loc[df["property_id"] != "A", "valid"].any()

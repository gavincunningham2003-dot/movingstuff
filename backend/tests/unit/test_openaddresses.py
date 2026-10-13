import gzip
import json

import pandas as pd
import pytest

from catrix.ingest.http import SourceUnavailableError
from catrix.ingest.openaddresses import parse_openaddresses_file, stratified_sample

# SYNTHETIC OpenAddresses-shaped records (not real addresses).
GEOJSON_ROWS = [
    {
        "type": "Feature",
        "properties": {
            "number": "100",
            "street": "MAIN ST",
            "unit": "",
            "city": "BERKELEY",
            "district": "",
            "region": "CA",
            "postcode": "94704",
            "id": "a1",
            "hash": "h1",
        },
        "geometry": {"type": "Point", "coordinates": [-122.27, 37.87]},
    },
    {
        "type": "Feature",
        "properties": {
            "number": "101",
            "street": "MAIN ST",
            "unit": "B",
            "city": "BERKELEY",
            "district": "",
            "region": "CA",
            "postcode": "94704",
            "id": "a2",
            "hash": "h2",
        },
        "geometry": {"type": "Point", "coordinates": [-122.271, 37.871]},
    },
]


def test_parse_ndjson_gz(tmp_path):
    p = tmp_path / "source.geojson.gz"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        for r in GEOJSON_ROWS:
            fh.write(json.dumps(r) + "\n")
    df = parse_openaddresses_file(p)
    assert len(df) == 2
    assert df.loc[0, "lon"] == -122.27 and df.loc[1, "unit"] == "B"
    assert df["source_file"].iloc[0] == "source.geojson.gz"


def test_parse_feature_collection(tmp_path):
    p = tmp_path / "fc.geojson"
    p.write_text(
        json.dumps({"type": "FeatureCollection", "features": GEOJSON_ROWS}), encoding="utf-8"
    )
    assert len(parse_openaddresses_file(p)) == 2


def test_parse_legacy_csv(tmp_path):
    p = tmp_path / "alameda.csv"
    p.write_text(
        "LON,LAT,NUMBER,STREET,UNIT,CITY,DISTRICT,REGION,POSTCODE,ID,HASH\n-122.27,37.87,100,MAIN ST,,BERKELEY,,CA,94704,a1,h1\n",
        encoding="utf-8",
    )
    df = parse_openaddresses_file(p)
    assert df.loc[0, "number"] == "100" and df.loc[0, "lat"] == 37.87


def test_parse_csv_missing_columns_raises(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(SourceUnavailableError):
        parse_openaddresses_file(p)


def test_stratified_sample_spreads_across_files():
    df = pd.DataFrame({"source_file": ["a"] * 1000 + ["b"] * 100 + ["c"] * 10, "x": range(1110)})
    out = stratified_sample(df, 300, seed=1)
    counts = out["source_file"].value_counts()
    assert counts["c"] == 10 and counts["b"] == 100 and counts["a"] == 190
    assert len(out) == 300

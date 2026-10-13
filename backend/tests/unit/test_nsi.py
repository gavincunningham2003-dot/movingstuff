import json

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Point

from catrix.ingest.http import SourceUnavailableError
from catrix.ingest.nsi import match_addresses_to_structures, parse_nsi_geojson, structures_from_gdf

# SYNTHETIC NSI-shaped features (schema mirrors the verified fips=06003 response).
FEATURES = [
    {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-122.2700, 37.8700]},
        "properties": {
            "fd_id": 1,
            "bid": "x",
            "bldgtype": "W",
            "occtype": "RES1-1SNB",
            "st_damcat": "RES",
            "num_story": 1,
            "sqft": 1500,
            "found_type": "S",
            "found_ht": 1.0,
            "ground_elv": 120.5,
            "med_yr_blt": 1962,
            "val_struct": 350000.5,
            "val_cont": 175000.0,
            "firmzone": "X",
            "static_bfe": -9999,
        },
    },
    {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-122.2750, 37.8750]},
        "properties": {
            "fd_id": 2,
            "bid": "y",
            "bldgtype": "M",
            "occtype": "COM1",
            "st_damcat": "COM",
            "num_story": 2,
            "sqft": 8000,
            "found_type": "B",
            "found_ht": 0.0,
            "ground_elv": 118.0,
            "med_yr_blt": 1925,
            "val_struct": 900000.0,
            "val_cont": 500000.0,
            "firmzone": "AE",
            "static_bfe": 121.0,
        },
    },
]


def test_parse_and_convert(tmp_path):
    p = tmp_path / "nsi.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": FEATURES}), encoding="utf-8")
    gdf = parse_nsi_geojson(p)
    rows = structures_from_gdf(gdf, "06001")
    assert len(rows) == 2
    r = {x["id"]: x for x in rows}
    assert r[1]["static_bfe_ft"] is None and r[2]["static_bfe_ft"] == 121.0
    assert (
        r[1]["year_built"] == 1962
        and r[2]["found_type"] == "B"
        and r[2]["damage_category"] == "COM"
    )
    assert r[1]["geom"] == "SRID=4326;POINT(-122.27 37.87)"


def test_parse_rejects_schema_drift(tmp_path):
    p = tmp_path / "bad.geojson"
    p.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [0, 0]},
                        "properties": {"foo": 1},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SourceUnavailableError, match="schema drift"):
        parse_nsi_geojson(p)


def test_matching_uncertainty_classes():
    structures = gpd.GeoDataFrame(
        {"structure_id": [1, 2]},
        geometry=[Point(-122.2700, 37.8700), Point(-122.2750, 37.8750)],
        crs="EPSG:4326",
    )
    addresses = gpd.GeoDataFrame(
        {"address_id": [10, 11, 12]},
        geometry=[
            Point(-122.2700, 37.8700),
            Point(-122.2700 + 0.0001, 37.8700),
            Point(-122.26, 37.86),
        ],
        crs="EPSG:4326",
    )
    m = match_addresses_to_structures(addresses, structures).set_index("address_id")
    assert m.loc[10, "uncertainty"] == "low" and m.loc[10, "structure_id"] == 1
    assert (
        m.loc[11, "uncertainty"] in ("medium", "high") and 0 < m.loc[11, "match_distance_m"] <= 30
    )
    assert m.loc[12, "uncertainty"] == "unmatched" and np.isnan(m.loc[12, "match_distance_m"])

import geopandas as gpd
import numpy as np
from shapely.geometry import Point, Polygon

from catrix.ingest.buildings import match_addresses_to_buildings


def _square(lon, lat, half_deg=0.0001):
    return Polygon(
        [
            (lon - half_deg, lat - half_deg),
            (lon + half_deg, lat - half_deg),
            (lon + half_deg, lat + half_deg),
            (lon - half_deg, lat + half_deg),
        ]
    )


def test_matching_contains_nearest_and_unmatched():
    # SYNTHETIC footprints near Berkeley.
    buildings = gpd.GeoDataFrame(
        {"building_id": [1, 2]},
        geometry=[_square(-122.2700, 37.8700), _square(-122.2710, 37.8700)],
        crs="EPSG:4326",
    )
    addresses = gpd.GeoDataFrame(
        {"address_id": [10, 11, 12]},
        geometry=[
            Point(-122.2700, 37.8700),
            Point(-122.2700 + 0.00015, 37.8700),
            Point(-122.2500, 37.8700),
        ],
        crs="EPSG:4326",
    )
    m = match_addresses_to_buildings(addresses, buildings).set_index("address_id")
    assert (
        m.loc[10, "match_method"] == "contains"
        and m.loc[10, "uncertainty"] == "low"
        and m.loc[10, "building_id"] == 1
    )
    assert m.loc[11, "match_method"] == "nearest" and m.loc[11, "uncertainty"] in ("medium", "high")
    assert 0 < m.loc[11, "match_distance_m"] <= 30
    assert (
        m.loc[12, "match_method"] == "none"
        and m.loc[12, "uncertainty"] == "unmatched"
        and np.isnan(m.loc[12, "match_distance_m"])
    )
    assert m.loc[10, "buildings_within_100m"] == 2
    assert m.loc[12, "buildings_within_100m"] == 0


def test_matching_with_no_buildings_returns_unmatched():
    addresses = gpd.GeoDataFrame(
        {"address_id": [1]}, geometry=[Point(-122.27, 37.87)], crs="EPSG:4326"
    )
    empty = gpd.GeoDataFrame({"building_id": []}, geometry=[], crs="EPSG:4326")
    m = match_addresses_to_buildings(addresses, empty)
    assert m["match_method"].tolist() == ["none"]

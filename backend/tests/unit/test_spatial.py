import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Polygon

from catrix.geo.spatial import (
    count_within_radius,
    grid_cell_centroid,
    grid_cell_id,
    grid_cell_ids,
    nearest_distance_m,
    point_in_polygon_join,
    points_gdf,
)


def test_point_in_polygon_join_assigns_classes_and_counts(synthetic_points, synthetic_polygons):
    pts = points_gdf(synthetic_points)
    joined = point_in_polygon_join(pts, synthetic_polygons, ["classification", "class_code"])
    assert joined["classification"].tolist()[:2] == ["Very High", "High"]
    assert joined["classification"].isna().tolist()[2:] == [True, True]
    assert joined["_n_matches"].tolist()[:2] == [1, 1]


def test_point_in_polygon_join_priority_on_overlap(synthetic_points):
    overlapping = gpd.GeoDataFrame(
        {"classification": ["Moderate", "Very High"]},
        geometry=[
            Polygon([(-123, 37), (-122, 37), (-122, 38.5), (-123, 38.5)]),
            Polygon([(-122.3, 37.8), (-122.2, 37.8), (-122.2, 37.9), (-122.3, 37.9)]),
        ],
        crs="EPSG:4326",
    )
    pts = points_gdf(synthetic_points.iloc[[0]])
    joined = point_in_polygon_join(
        pts,
        overlapping,
        ["classification"],
        priority="classification",
        priority_order=["Very High", "High", "Moderate"],
    )
    assert joined["classification"].iloc[0] == "Very High"
    assert joined["_n_matches"].iloc[0] == 2


def test_nearest_distance_in_metres_and_max_distance(synthetic_points):
    pts = points_gdf(synthetic_points.iloc[[0]])
    # A north-south line 0.01 deg (~880 m) east of the point.
    lines = gpd.GeoDataFrame(
        geometry=[LineString([(-122.26, 37.0), (-122.26, 38.5)])], crs="EPSG:4326"
    )
    res = nearest_distance_m(pts, lines)
    assert 850 < res.distance_m[0] < 920
    capped = nearest_distance_m(pts, lines, max_distance_m=100)
    assert capped.index[0] == -1 and np.isnan(capped.distance_m[0])


def test_count_within_radius(synthetic_points, synthetic_polygons):
    pts = points_gdf(synthetic_points)
    counts = count_within_radius(pts, synthetic_polygons, 1000)
    assert counts.tolist() == [1, 1, 0, 0]


def test_grid_cells_are_stable_and_centroid_roundtrips():
    cid = grid_cell_id(-122.27, 37.87, cell_km=10)
    assert grid_cell_ids(np.array([-122.27]), np.array([37.87]), 10)[0] == cid
    lon, lat = grid_cell_centroid(cid, 10)
    assert abs(lon + 122.27) < 0.1 and abs(lat - 37.87) < 0.1

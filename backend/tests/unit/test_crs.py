import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Point

from catrix.geo.crs import ensure_crs, lonlat_to_projected, metres_per_degree, to_projected


def test_projected_distance_is_in_metres():
    # Two points ~1 km apart along a meridian near San Francisco.
    a = to_projected(Point(-122.4194, 37.7749))
    b = to_projected(Point(-122.4194, 37.7749 + 1 / 111.0))
    assert abs(a.distance(b) - 1000) < 15


def test_lonlat_to_projected_vectorised_matches_scalar():
    x, y = lonlat_to_projected(np.array([-122.4194]), np.array([37.7749]))
    p = to_projected(Point(-122.4194, 37.7749))
    assert abs(x[0] - p.x) < 1e-6 and abs(y[0] - p.y) < 1e-6


def test_metres_per_degree_reasonable_at_38n():
    m_lon, m_lat = metres_per_degree(38.0)
    assert 110_900 < m_lat < 111_200
    assert 87_000 < m_lon < 88_500


def test_ensure_crs_sets_geographic_when_missing_and_rejects_projected_without_crs():
    gdf = gpd.GeoDataFrame(geometry=[Point(-122.0, 37.0)])
    assert ensure_crs(gdf).crs.to_epsg() == 4326
    bad = gpd.GeoDataFrame(geometry=[Point(-200000.0, 100000.0)])
    with pytest.raises(ValueError):
        ensure_crs(bad)


def test_ensure_crs_reprojects():
    gdf = gpd.GeoDataFrame(geometry=[Point(-122.0, 37.0)], crs="EPSG:4326")
    out = ensure_crs(gdf, "EPSG:3310")
    assert out.crs.to_epsg() == 3310
    assert abs(out.geometry.iloc[0].x) > 1000

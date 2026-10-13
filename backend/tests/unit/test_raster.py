import numpy as np
import rasterio
from rasterio.transform import from_origin

from catrix.geo.raster import dem_tile_id, horn_slope_aspect, sample_categorical, sample_terrain


def test_horn_slope_flat_and_inclined():
    flat = np.full((3, 3), 100.0)
    s, a = horn_slope_aspect(flat, 30, 30)
    assert s == 0 and a == -1
    # Rises 10 m per 30 m column towards the east -> ~18.4 deg, descent to the west (270).
    east = np.array([[0, 10, 20], [0, 10, 20], [0, 10, 20]], dtype=float)
    s, a = horn_slope_aspect(east, 30, 30)
    assert abs(s - 18.43) < 0.1
    assert abs(a - 270) < 1e-6
    # Rises towards the south -> descent faces north (0).
    south = np.array([[0, 0, 0], [10, 10, 10], [20, 20, 20]], dtype=float)
    s, a = horn_slope_aspect(south, 30, 30)
    assert abs(a - 0) < 1e-6


def _write_dem(path, data, nodata=None, crs="EPSG:4326", res=1 / 3600):
    transform = from_origin(-122.5, 38.0, res, res)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(data.astype("float32"), 1)


def test_sample_terrain_geographic_raster(tmp_path):
    # 10x10 pixel DEM rising 1 m per pixel eastwards; pixel ~ 30.9 m x 24.3 m at 38N.
    data = np.tile(np.arange(10, dtype=float), (10, 1))
    p = tmp_path / "dem.tif"
    _write_dem(p, data)
    with rasterio.open(p) as ds:
        res = sample_terrain(ds, -122.5 + 5.5 / 3600, 38.0 - 5.5 / 3600)
    assert res.status == "ok"
    assert res.elevation_m == 5.0
    # 1 m rise per ~24.3 m (lon spacing at 38N) -> ~2.36 deg
    assert 2.0 < res.slope_deg < 2.7
    assert abs(res.aspect_deg - 270) < 1


def test_sample_terrain_nodata_edge_and_outside(tmp_path):
    data = np.full((5, 5), 10.0)
    data[2, 2] = -9999
    p = tmp_path / "dem.tif"
    _write_dem(p, data, nodata=-9999)
    with rasterio.open(p) as ds:
        assert sample_terrain(ds, -122.5 + 2.5 / 3600, 38.0 - 2.5 / 3600).status == "nodata"
        assert sample_terrain(ds, -122.5 + 0.5 / 3600, 38.0 - 0.5 / 3600).status == "edge"
        assert sample_terrain(ds, -100.0, 10.0).status == "outside"


def test_sample_categorical(tmp_path):
    data = np.full((5, 5), 42.0)
    p = tmp_path / "lc.tif"
    _write_dem(p, data)
    with rasterio.open(p) as ds:
        assert sample_categorical(ds, -122.5 + 2.5 / 3600, 38.0 - 2.5 / 3600) == 42
        assert sample_categorical(ds, -100.0, 10.0) is None


def test_dem_tile_id_convention():
    assert dem_tile_id(-122.4194, 37.7749) == "n38w123"
    assert dem_tile_id(-118.25, 34.05) == "n35w119"
    assert dem_tile_id(-120.0, 39.0) == "n40w120"

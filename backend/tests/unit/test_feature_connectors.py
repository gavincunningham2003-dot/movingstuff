"""Year-matched feature connectors: pure functions over SYNTHETIC inputs (no network)."""

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

from catrix.geo.raster import sample_values, window_fraction
from catrix.ingest.drought import parse_usdm_csv, yearly_drought_features
from catrix.ingest.landfire import fuel_group, is_burnable
from catrix.ingest.nlcd import features_from_raster, tile_bbox_5070, tile_id, wcs_url
from catrix.ingest.prism import derive_climate_features
from catrix.ml.optional_features import load_optional_features, match_version_year

USDM_CSV = """MapDate,FIPS,County,State,None,D0,D1,D2,D3,D4,ValidStart,ValidEnd,StatisticFormatID
20210223,06001,Alameda County,CA,0.00,100.00,100.00,21.01,0.00,0.00,2021-02-23,2021-03-01,1
20210216,06001,Alameda County,CA,0.00,100.00,100.00,21.01,0.00,0.00,2021-02-16,2021-02-22,1
20210126,06001,Alameda County,CA,0.00,100.00,100.00,100.00,40.00,0.00,2021-01-26,2021-02-01,1
20201229,06001,Alameda County,CA,50.00,50.00,0.00,0.00,0.00,0.00,2020-12-29,2021-01-04,1
"""


def test_usdm_parsing_and_yearly_aggregation():
    weekly = parse_usdm_csv(USDM_CSV)
    feats = yearly_drought_features(weekly).set_index(["county_fips", "feature_year"])
    assert feats.loc[("06001", 2021), "drought_d2_share"] == pytest.approx(
        (21.01 + 21.01 + 100.0) / 3
    )
    assert feats.loc[("06001", 2021), "drought_d3_max"] == 40.0
    assert feats.loc[("06001", 2021), "drought_weeks_d2"] == 3
    assert feats.loc[("06001", 2020), "drought_d2_share"] == 0.0


def test_prism_anomalies_against_1991_2020_mean():
    years = list(range(1991, 2023))
    ppt = [500.0] * 30 + [250.0, 1000.0]  # 2021 half of normal, 2022 double
    tmax = [20.0] * 30 + [22.0, 19.0]
    samples = pd.DataFrame({"address_id": 1, "year": years, "ppt": ppt, "tmax": tmax})
    f = derive_climate_features(samples).set_index("feature_year")
    assert f.loc[2021, "precip_pct_normal"] == pytest.approx(50.0)
    assert f.loc[2022, "precip_pct_normal"] == pytest.approx(200.0)
    assert f.loc[2022, "precip_prev_year_pct_normal"] == pytest.approx(50.0)
    assert f.loc[2022, "precip_3y_pct_normal"] == pytest.approx((100 + 50 + 200) / 3)
    assert f.loc[2021, "tmax_anomaly_c"] == pytest.approx(2.0)
    assert np.isnan(f.loc[1991, "precip_prev_year_pct_normal"])


def test_fuel_group_mapping():
    assert fuel_group(91) == 0 and is_burnable(91) == 0
    assert fuel_group(102) == 1 and fuel_group(122) == 2 and fuel_group(145) == 3
    assert fuel_group(163) == 4 and fuel_group(185) == 5 and fuel_group(203) == 6
    assert fuel_group(50) is None and is_burnable(None) is None


def test_nlcd_tiling_and_wcs_url():
    tid = tile_id(-122.27, 37.87)
    assert tid == "-245_75"
    bb = tile_bbox_5070(tid)
    assert bb[0] < bb[2] and bb[1] < bb[3] and abs(bb[2] - bb[0]) > 40_000  # ~0.5 deg in metres
    url = wcs_url(2021, bb)
    assert "coverageId=mrlc_download__NLCD_2021_Land_Cover_L48" in url and "subset=X(" in url


def _write_lc(path, data, crs="EPSG:5070", res=30.0):
    transform = from_origin(-2_200_000, 2_000_000, res, res)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="int16",
        crs=crs,
        transform=transform,
        nodata=0,
    ) as dst:
        dst.write(data.astype("int16"), 1)


def test_sample_values_and_window_fraction_and_nlcd_features(tmp_path):
    data = np.full((60, 60), 42, dtype=int)  # evergreen forest
    data[:, 30:] = 23  # developed, medium intensity on the east half
    p = tmp_path / "lc.tif"
    _write_lc(p, data)
    with rasterio.open(p) as ds:
        # Centre of pixel (row 30, col 15) -> forest; (row 30, col 45) -> developed.
        from pyproj import Transformer

        t = Transformer.from_crs("EPSG:5070", "EPSG:4326", always_xy=True)
        xs = [-2_200_000 + 15.5 * 30, -2_200_000 + 45.5 * 30]
        ys = [2_000_000 - 30.5 * 30, 2_000_000 - 30.5 * 30]
        lons, lats = t.transform(xs, ys)
        vals = sample_values(ds, np.array(lons), np.array(lats))
        assert vals.tolist() == [42.0, 23.0]
        dev = window_fraction(
            ds,
            np.array(lons),
            np.array(lats),
            radius_m=300.0,
            predicate=lambda a: (a >= 21) & (a <= 24),
        )
        assert dev[0] < 0.5 < dev[1]
        outside = sample_values(ds, np.array([-100.0]), np.array([10.0]))
        assert np.isnan(outside[0])
    feats = features_from_raster(p, np.array([1, 2]), np.array(lons), np.array(lats), 2021)
    assert feats["lc_code"].tolist() == [42, 23]
    assert feats["lc_developed_intensity"].tolist() == [0, 3]
    assert feats.loc[0, "lc_veg_frac_500m"] > feats.loc[1, "lc_veg_frac_500m"]


def test_match_version_year_and_lag():
    assert match_version_year(2018, [2001, 2016, 2022]) == (2016, 2)
    assert match_version_year(2016, [2001, 2016, 2022]) == (2016, 0)
    assert match_version_year(1995, [2001, 2016]) == (2001, 6)
    assert match_version_year(2030, [2001, 2016, 2022]) == (2022, 8)


def test_load_optional_features_merges_and_expands(tmp_path):
    from catrix.ingest.store import write_parquet

    write_parquet(
        pd.DataFrame(
            {
                "address_id": [1, 1],
                "feature_year": [2000, 2001],
                "precip_pct_normal": [80.0, 120.0],
                "precip_prev_year_pct_normal": [np.nan, 80.0],
                "precip_3y_pct_normal": [np.nan, 100.0],
                "tmax_anomaly_c": [0.5, -0.2],
            }
        ),
        "climate_features",
        subdir="ml",
    )
    write_parquet(
        pd.DataFrame(
            {
                "county_fips": ["06001"],
                "feature_year": [2001],
                "drought_d2_share": [30.0],
                "drought_d3_max": [10.0],
                "drought_weeks_d2": [12],
            }
        ),
        "drought_features",
        subdir="ml",
    )
    write_parquet(
        pd.DataFrame(
            {
                "address_id": [1, 1],
                "lf_version": ["LF2001", "LF2016"],
                "data_year": [2001, 2016],
                "fbfm40": [122, 145],
                "fuel_group": [2, 3],
                "fuel_burnable": [1, 1],
                "canopy_cover_pct": [10.0, 35.0],
                "burnable_frac_500m": [0.6, 0.7],
            }
        ),
        "fuel_features",
        subdir="ml",
    )
    addresses = pd.DataFrame({"address_id": [1], "county_fips": ["06001"]})
    out = load_optional_features(addresses, (1999, 2017)).set_index("feature_year")
    assert out.loc[2001, "precip_pct_normal"] == 120.0 and np.isnan(
        out.loc[1999, "precip_pct_normal"]
    )
    assert out.loc[2001, "drought_d2_share"] == 30.0 and np.isnan(out.loc[2000, "drought_d2_share"])
    assert out.loc[1999, "fuel_group"] == 2 and out.loc[1999, "fuel_version_lag_years"] == 2
    assert out.loc[2010, "fuel_group"] == 2 and out.loc[2010, "fuel_version_lag_years"] == 9
    assert out.loc[2016, "fuel_group"] == 3 and out.loc[2016, "fuel_version_lag_years"] == 0
    assert out.loc[2017, "fuel_group"] == 3 and out.loc[2017, "fuel_version_lag_years"] == 1
    assert "lc_developed_intensity" not in out.columns  # no land-cover table written

"""Opt-in live verification of the public data services (network required).

Run with:  pytest -m live tests/live
"""

from __future__ import annotations

import pytest

from catrix.ingest.arcgis import (
    imageserver_sample,
    layer_metadata,
    mapserver_identify_raster,
    query_point,
    validate_fields,
)
from catrix.ingest.http import get_json
from catrix.ingest.registry import load_registry

pytestmark = pytest.mark.live


def test_calfire_perimeters_schema_and_count():
    spec = load_registry().get("calfire_fire_perimeters")
    meta = layer_metadata(spec.id, spec.api_url)
    validate_fields(spec.id, spec.api_url, meta, ["YEAR_", "FIRE_NAME", "GIS_ACRES", "ALARM_DATE"])
    count = get_json(
        spec.id,
        f"{spec.api_url}/query",
        params={"where": "1=1", "returnCountOnly": "true", "f": "json"},
    )["count"]
    assert count > 20_000


def test_fhsz_point_query_sierra_foothills():
    spec = load_registry().get("calfire_fhsz_2024")
    rows = query_point(spec.id, spec.api_url, -120.65, 38.75, ["FHSZ", "FHSZ_Description"])
    assert rows and rows[0]["FHSZ_Description"] in ("Moderate", "High", "Very High")


def test_cgs_pga_and_landslide():
    pga = load_registry().get("cgs_ms48_pga_2pc50")
    assert 0.5 < imageserver_sample(pga.id, pga.api_url, -122.18, 37.85) < 2.0
    ls = load_registry().get("cgs_landslide_susceptibility_ms58")
    attrs = mapserver_identify_raster(ls.id, ls.api_url, 0, -122.18, 37.85)
    assert attrs and attrs.get("Raster.Value") is not None


def test_usgs_designmaps_pgauh():
    spec = load_registry().get("usgs_designmaps_asce7_16")
    data = get_json(spec.id, spec.api_url.format(lat=37.7749, lon=-122.4194))
    assert 0.3 < data["response"]["data"]["pgauh"] < 1.5


def test_epqs_elevation():
    spec = load_registry().get("usgs_epqs")
    data = get_json(spec.id, spec.api_url.format(lon=-122.4194, lat=37.7749))
    assert 0 < float(data["value"]) < 100

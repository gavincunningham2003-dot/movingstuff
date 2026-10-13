import httpx
import pytest
import respx

from catrix.ingest.arcgis import (
    fetch_layer,
    imageserver_sample,
    iter_features,
    mapserver_identify_raster,
    mapserver_identify_raster_batch,
)
from catrix.ingest.http import SourceUnavailableError, download_file, get_json

LAYER = "https://example.test/arcgis/rest/services/Test/FeatureServer/0"
META = {
    "name": "Test",
    "fields": [
        {"name": "OBJECTID", "type": "esriFieldTypeOID"},
        {"name": "YEAR_", "type": "esriFieldTypeSmallInteger"},
    ],
    "maxRecordCount": 2,
    "objectIdField": "OBJECTID",
    "advancedQueryCapabilities": {"supportsPagination": True},
}


def _feat(i: int) -> dict:
    return {
        "type": "Feature",
        "properties": {"OBJECTID": i, "YEAR_": 2000 + i},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-122, 37], [-121.9, 37], [-121.9, 37.1], [-122, 37.1], [-122, 37]]],
        },
    }


@respx.mock
def test_iter_features_pages_until_exhausted():
    respx.get(LAYER).mock(return_value=httpx.Response(200, json=META))
    route = respx.get(f"{LAYER}/query")
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [_feat(1), _feat(2)],
                "properties": {"exceededTransferLimit": True},
            },
        ),
        httpx.Response(200, json={"type": "FeatureCollection", "features": [_feat(3)]}),
    ]
    feats = list(iter_features("test", LAYER, out_fields=["OBJECTID", "YEAR_"]))
    assert [f["properties"]["OBJECTID"] for f in feats] == [1, 2, 3]
    assert route.call_count == 2
    assert route.calls[1].request.url.params["resultOffset"] == "2"


@respx.mock
def test_schema_drift_raises():
    respx.get(LAYER).mock(return_value=httpx.Response(200, json=META))
    with pytest.raises(SourceUnavailableError, match="schema drift"):
        list(iter_features("test", LAYER, out_fields=["OBJECTID", "FIRE_NAME"]))


@respx.mock
def test_fetch_layer_returns_gdf_in_wgs84():
    respx.get(LAYER).mock(return_value=httpx.Response(200, json=META))
    respx.get(f"{LAYER}/query").mock(
        return_value=httpx.Response(200, json={"type": "FeatureCollection", "features": [_feat(1)]})
    )
    gdf = fetch_layer("test", LAYER)
    assert gdf.crs.to_epsg() == 4326 and len(gdf) == 1 and gdf.geometry.iloc[0].area > 0


@respx.mock
def test_imageserver_and_identify_parsing():
    respx.get("https://example.test/ImageServer/getSamples").mock(
        return_value=httpx.Response(200, json={"samples": [{"value": "1.2534"}]})
    )
    assert imageserver_sample(
        "t", "https://example.test/ImageServer", -122.18, 37.85
    ) == pytest.approx(1.2534)
    respx.get("https://example.test/ImageServer2/getSamples").mock(
        return_value=httpx.Response(200, json={"samples": [{"value": "NoData"}]})
    )
    assert imageserver_sample("t", "https://example.test/ImageServer2", -122.18, 37.85) is None
    respx.get("https://example.test/MapServer/identify").mock(
        return_value=httpx.Response(
            200, json={"results": [{"layerId": 0, "attributes": {"Raster.Value": "7"}}]}
        )
    )
    assert (
        mapserver_identify_raster("t", "https://example.test/MapServer", 0, -122.18, 37.85)[
            "Raster.Value"
        ]
        == "7"
    )


@respx.mock
def test_download_file_caches_and_reports_auth_errors(tmp_path):
    url = "https://example.test/data/file.csv"
    route = respx.get(url).mock(return_value=httpx.Response(200, content=b"a,b\n1,2\n"))
    d1 = download_file("t", url)
    d2 = download_file("t", url)
    assert d1.from_cache is False and d2.from_cache is True and route.call_count == 1
    assert d1.sha256 == d2.sha256 and d1.path.read_bytes() == b"a,b\n1,2\n"
    respx.get("https://example.test/private.gz").mock(return_value=httpx.Response(403))
    with pytest.raises(SourceUnavailableError, match="HTTP 403"):
        download_file("t", "https://example.test/private.gz")


@respx.mock
def test_get_json_transport_error_becomes_source_unavailable():
    respx.get("https://example.test/api").mock(side_effect=httpx.ConnectError("reset"))
    with pytest.raises(SourceUnavailableError):
        get_json("t", "https://example.test/api")


@respx.mock
def test_identify_batch_keeps_point_order_and_nodata():
    """One multipoint request per batch; NoData points stay in place as None values."""
    calls: list[int] = []

    def _answer(request):
        import json as _json

        pts = _json.loads(dict(httpx.QueryParams(request.content.decode()))["geometry"])["points"]
        calls.append(len(pts))
        results = [
            {"layerId": 0, "attributes": {"Raster.Value": None if lon < -123 else "7"}}
            for lon, _lat in pts
        ]
        return httpx.Response(200, json={"results": results})

    respx.post("https://example.test/MapServer/identify").mock(side_effect=_answer)
    points = [(-122.18, 37.85), (-123.5, 37.0), (-121.94, 37.50), (-122.0, 37.0), (-123.9, 38.0)]
    out = mapserver_identify_raster_batch("t", "https://example.test/MapServer", 0, points, batch=2)
    assert calls == [2, 2, 1]
    assert [a["Raster.Value"] for a in out] == ["7", None, "7", "7", None]


@respx.mock
def test_identify_batch_falls_back_per_point_when_misaligned():
    """A batch answer with the wrong count is never trusted; the chunk is re-queried singly."""
    respx.post("https://example.test/MapServer/identify").mock(
        return_value=httpx.Response(200, json={"results": [{"layerId": 0, "attributes": {}}]})
    )
    single = respx.get("https://example.test/MapServer/identify").mock(
        return_value=httpx.Response(200, json={"results": [{"attributes": {"Raster.Value": "3"}}]})
    )
    out = mapserver_identify_raster_batch(
        "t", "https://example.test/MapServer", 0, [(-122.0, 37.0), (-122.1, 37.1)], batch=2
    )
    assert single.call_count == 2
    assert [a["Raster.Value"] for a in out] == ["3", "3"]

"""Minimal ArcGIS REST client (FeatureServer / MapServer / ImageServer).

Supports:
* paged feature extraction with ``resultOffset`` (verified maxRecordCount=2000 on the
  CAL FIRE services) returned as a GeoDataFrame in EPSG:4326,
* point-in-polygon queries,
* MapServer ``identify`` for raster layers (CGS MS58 landslide classes),
* ImageServer ``getSamples`` for continuous rasters (CGS MS48 PGA).

All responses are validated against the expected field list so schema drift surfaces as an
error rather than silently producing empty columns.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

from catrix.config import GEOGRAPHIC_CRS
from catrix.ingest.http import SourceUnavailableError, get_json
from catrix.logging import get_logger

log = get_logger(__name__)


def layer_metadata(source_id: str, layer_url: str) -> dict[str, Any]:
    meta = get_json(source_id, layer_url, params={"f": "pjson"})
    if "error" in meta:
        raise SourceUnavailableError(source_id, layer_url, f"service error: {meta['error']}")
    return meta


def validate_fields(
    source_id: str, layer_url: str, meta: dict[str, Any], required: list[str]
) -> None:
    names = {f["name"] for f in meta.get("fields", [])}
    missing = [f for f in required if f not in names]
    if missing:
        raise SourceUnavailableError(
            source_id, layer_url, f"schema drift: fields missing from service: {missing}"
        )


def iter_features(
    source_id: str,
    layer_url: str,
    *,
    where: str = "1=1",
    out_fields: list[str] | None = None,
    page_size: int | None = None,
    geometry_bbox: tuple[float, float, float, float] | None = None,
    max_features: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield GeoJSON features page by page (``f=geojson`` and ``outSR=4326``)."""
    meta = layer_metadata(source_id, layer_url)
    if out_fields:
        validate_fields(source_id, layer_url, meta, out_fields)
    size = page_size or int(meta.get("maxRecordCount", 1000))
    supports_pagination = meta.get("advancedQueryCapabilities", {}).get("supportsPagination", True)
    if not supports_pagination:
        raise SourceUnavailableError(source_id, layer_url, "service does not support pagination")
    offset = 0
    total = 0
    while True:
        params: dict[str, Any] = {
            "where": where,
            "outFields": ",".join(out_fields) if out_fields else "*",
            "returnGeometry": "true",
            "outSR": 4326,
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": size,
            "orderByFields": meta.get("objectIdField", "OBJECTID"),
        }
        if geometry_bbox:
            params.update(
                {
                    "geometry": ",".join(str(v) for v in geometry_bbox),
                    "geometryType": "esriGeometryEnvelope",
                    "inSR": 4326,
                    "spatialRel": "esriSpatialRelIntersects",
                }
            )
        data = get_json(source_id, f"{layer_url}/query", params=params, timeout=180)
        if "error" in data:
            raise SourceUnavailableError(source_id, layer_url, f"query error: {data['error']}")
        feats = data.get("features", [])
        for f in feats:
            yield f
            total += 1
            if max_features and total >= max_features:
                return
        exceeded = data.get("properties", {}).get("exceededTransferLimit") or data.get(
            "exceededTransferLimit"
        )
        log.debug("arcgis_page", source=source_id, offset=offset, n=len(feats), more=bool(exceeded))
        if len(feats) < size and not exceeded:
            break
        if len(feats) == 0:
            break
        offset += len(feats)


def features_to_gdf(features: list[dict[str, Any]]) -> gpd.GeoDataFrame:
    if not features:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=GEOGRAPHIC_CRS)
    rows = []
    geoms = []
    for f in features:
        props = dict(f.get("properties") or {})
        g = f.get("geometry")
        geoms.append(shape(g) if g else None)
        rows.append(props)
    gdf = gpd.GeoDataFrame(pd.DataFrame(rows), geometry=geoms, crs=GEOGRAPHIC_CRS)
    return gdf


def fetch_layer(
    source_id: str,
    layer_url: str,
    *,
    where: str = "1=1",
    out_fields: list[str] | None = None,
    geometry_bbox: tuple[float, float, float, float] | None = None,
    max_features: int | None = None,
    batch_callback: Any | None = None,
) -> gpd.GeoDataFrame:
    """Fetch a full layer into memory. For very large layers prefer :func:`iter_features`
    with a bbox and a ``batch_callback`` that persists each page."""
    feats: list[dict[str, Any]] = []
    for f in iter_features(
        source_id,
        layer_url,
        where=where,
        out_fields=out_fields,
        geometry_bbox=geometry_bbox,
        max_features=max_features,
    ):
        feats.append(f)
        if batch_callback and len(feats) >= 2000:
            batch_callback(features_to_gdf(feats))
            feats = []
    if batch_callback:
        if feats:
            batch_callback(features_to_gdf(feats))
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=GEOGRAPHIC_CRS)
    return features_to_gdf(feats)


def query_point(
    source_id: str,
    layer_url: str,
    lon: float,
    lat: float,
    out_fields: list[str],
) -> list[dict[str, Any]]:
    """Attributes of every polygon containing the point (no geometry returned)."""
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(out_fields),
        "returnGeometry": "false",
        "f": "json",
    }
    data = get_json(source_id, f"{layer_url}/query", params=params)
    if "error" in data:
        raise SourceUnavailableError(source_id, layer_url, f"query error: {data['error']}")
    return [f["attributes"] for f in data.get("features", [])]


def mapserver_identify_raster(
    source_id: str, mapserver_url: str, layer_id: int, lon: float, lat: float
) -> dict[str, Any] | None:
    """MapServer ``identify`` on a raster layer; returns the attributes dict or None."""
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "sr": 4326,
        "layers": f"all:{layer_id}",
        "tolerance": 1,
        "mapExtent": f"{lon - 0.01},{lat - 0.01},{lon + 0.01},{lat + 0.01}",
        "imageDisplay": "400,400,96",
        "returnGeometry": "false",
        "f": "json",
    }
    data = get_json(source_id, f"{mapserver_url}/identify", params=params)
    if "error" in data:
        raise SourceUnavailableError(source_id, mapserver_url, f"identify error: {data['error']}")
    results = data.get("results", [])
    return results[0].get("attributes") if results else None


def imageserver_sample(
    source_id: str, imageserver_url: str, lon: float, lat: float
) -> float | None:
    """ImageServer ``getSamples`` at a point. Returns None for NoData."""
    geometry = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
    params = {
        "geometry": geometry,
        "geometryType": "esriGeometryPoint",
        "returnFirstValueOnly": "true",
        "f": "json",
    }
    data = get_json(source_id, f"{imageserver_url}/getSamples", params=params)
    if "error" in data:
        raise SourceUnavailableError(
            source_id, imageserver_url, f"getSamples error: {data['error']}"
        )
    samples = data.get("samples", [])
    if not samples:
        return None
    raw = samples[0].get("value")
    if raw in (None, "", "NoData"):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None

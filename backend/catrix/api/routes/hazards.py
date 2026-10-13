from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from catrix.api.deps import DB, BBox, parse_bbox
from catrix.api.schemas import HazardCatalogueOut, HazardLayerFeatureCollection
from catrix.hazards.registry import catalogue

router = APIRouter(prefix="/hazards", tags=["hazards"])

LEGENDS: dict[str, dict[str, Any]] = {
    "wildfire_fhsz": {
        "table": "hazard_geometries",
        "hazard_id": "wildfire_fhsz",
        "source_id": "calfire_fhsz_2024",
        "class_col": "classification",
        "legend": [
            {"label": "Very High", "color": "#dc2626", "value": "Very High"},
            {"label": "High", "color": "#f97316", "value": "High"},
            {"label": "Moderate", "color": "#facc15", "value": "Moderate"},
        ],
    },
    "flood_nfhl": {
        "table": "hazard_geometries",
        "hazard_id": "flood_nfhl",
        "source_id": "fema_nfhl",
        "class_col": "classification",
        "legend": [
            {"label": "SFHA (A/V zones, 1% annual chance)", "color": "#3b82f6", "value": "A"},
            {"label": "0.2% annual chance (shaded X)", "color": "#93c5fd", "value": "X_SHADED"},
            {"label": "Minimal (X)", "color": "#475569", "value": "X"},
        ],
    },
    "tsunami": {
        "table": "hazard_geometries",
        "hazard_id": "tsunami",
        "source_id": "cgs_tsunami_hazard_area",
        "class_col": "classification",
        "legend": [{"label": "Tsunami hazard area", "color": "#a855f7", "value": "Yes"}],
    },
    "liquefaction": {
        "table": "hazard_geometries",
        "hazard_id": "liquefaction",
        "source_id": "cgs_seismic_hazard_zones",
        "class_col": "classification",
        "legend": [{"label": "Liquefaction zone", "color": "#22d3ee", "value": "Liquefaction"}],
    },
    "fire_perimeters": {
        "table": "wildfire_events",
        "hazard_id": "wildfire_history",
        "source_id": "calfire_fire_perimeters",
        "class_col": "year",
        "legend": [
            {"label": "Perimeter 2015+", "color": "#ef4444", "value": "2015"},
            {"label": "Perimeter 2000-2014", "color": "#f59e0b", "value": "2000"},
            {"label": "Perimeter before 2000", "color": "#78716c", "value": "1950"},
        ],
    },
}
MAX_FEATURES = 3000


@router.get("", response_model=list[HazardCatalogueOut])
def list_hazards() -> list[HazardCatalogueOut]:
    return [
        HazardCatalogueOut(
            hazard_id=h.hazard_id,
            name=h.name,
            description=h.description,
            source_ids=list(h.source_ids),
            method=h.method,
            units=h.units,
            probability_horizon=h.probability_horizon,
            scale_note=h.scale_note,
            map_layer_available=h.map_layer_available,
        )
        for h in catalogue()
    ]


@router.get("/{hazard_id}/geojson", response_model=HazardLayerFeatureCollection)
def hazard_geojson(
    hazard_id: str,
    db: DB,
    bbox: BBox = None,
    zoom: float = Query(10, ge=0, le=22),
    min_year: int | None = None,
) -> HazardLayerFeatureCollection:
    spec = LEGENDS.get(hazard_id)
    if spec is None:
        raise HTTPException(404, f"no map layer for hazard '{hazard_id}'")
    bb = parse_bbox(bbox)
    if bb is None:
        raise HTTPException(422, "bbox is required for hazard layers")
    # Simplify geometry according to zoom (degrees per pixel ~ 360 / (256 * 2^z)).
    tol = 360.0 / (256 * 2**zoom) * 1.5
    minx, miny, maxx, maxy = bb
    if spec["table"] == "hazard_geometries":
        sql = text(
            f"""
            SELECT id, classification, class_code, attributes,
                   ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_Intersection(geom, ST_MakeEnvelope(:minx,:miny,:maxx,:maxy,4326)), :tol), 6) AS gj
            FROM hazard_geometries
            WHERE hazard_id = :hid AND geom && ST_MakeEnvelope(:minx,:miny,:maxx,:maxy,4326)
            ORDER BY class_code DESC NULLS LAST LIMIT {MAX_FEATURES + 1}
            """
        )
        rows = (
            db.execute(
                sql,
                {
                    "minx": minx,
                    "miny": miny,
                    "maxx": maxx,
                    "maxy": maxy,
                    "tol": tol,
                    "hid": spec["hazard_id"],
                },
            )
            .mappings()
            .all()
        )
        feats = [
            {
                "type": "Feature",
                "id": r["id"],
                "geometry": json.loads(r["gj"]),
                "properties": {
                    "classification": r["classification"],
                    "class_code": r["class_code"],
                    **(
                        {
                            k: v
                            for k, v in (r["attributes"] or {}).items()
                            if k
                            in (
                                "FLD_ZONE",
                                "ZONE_SUBTY",
                                "SFHA_TF",
                                "FHSZ_7Class",
                                "SRA22_2",
                                "County",
                                "Label",
                            )
                        }
                    ),
                },
            }
            for r in rows
            if r["gj"]
        ]
    else:
        sql = text(
            f"""
            SELECT id, year, fire_name, gis_acres,
                   ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_Intersection(geom, ST_MakeEnvelope(:minx,:miny,:maxx,:maxy,4326)), :tol), 6) AS gj
            FROM wildfire_events
            WHERE geom && ST_MakeEnvelope(:minx,:miny,:maxx,:maxy,4326) AND year >= :miny_year
            ORDER BY year DESC LIMIT {MAX_FEATURES + 1}
            """
        )
        rows = (
            db.execute(
                sql,
                {
                    "minx": minx,
                    "miny": miny,
                    "maxx": maxx,
                    "maxy": maxy,
                    "tol": tol,
                    "miny_year": min_year or 1950,
                },
            )
            .mappings()
            .all()
        )
        feats = [
            {
                "type": "Feature",
                "id": r["id"],
                "geometry": json.loads(r["gj"]),
                "properties": {
                    "year": r["year"],
                    "fire_name": r["fire_name"],
                    "gis_acres": r["gis_acres"],
                    "classification": "2015"
                    if (r["year"] or 0) >= 2015
                    else ("2000" if (r["year"] or 0) >= 2000 else "1950"),
                },
            }
            for r in rows
            if r["gj"]
        ]
    truncated = len(feats) > MAX_FEATURES
    return HazardLayerFeatureCollection(
        features=feats[:MAX_FEATURES],
        hazard_id=hazard_id,
        source_id=spec["source_id"],
        legend=spec["legend"],
        truncated=truncated,
    )

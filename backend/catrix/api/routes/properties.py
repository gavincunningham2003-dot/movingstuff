from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select, text

from catrix.api.deps import DB, BBox, parse_bbox
from catrix.api.schemas import (
    BuildingMatchOut,
    CompositeScoreOut,
    EnvironmentOut,
    FinancialScenarioSetOut,
    HazardAssessmentOut,
    PagedProperties,
    PropertyDetailOut,
    PropertySummaryOut,
    StructureAttributesOut,
    WildfirePredictionOut,
)
from catrix.config import get_settings
from catrix.db.models import (
    Address,
    AddressBuilding,
    AddressStructure,
    Building,
    EnvironmentalObservation,
    ModelVersion,
    Prediction,
    PropertyHazardAssessment,
    Region,
)
from catrix.finance.scenarios import compute_scenarios
from catrix.finance.vulnerability import BuildingProfile, hazus_building_type
from catrix.hazards.base import HazardResult
from catrix.hazards.registry import catalogue
from catrix.scoring.composite import compute_composite

router = APIRouter(prefix="/properties", tags=["properties"])
HAZARD_NAMES = {h.hazard_id: h.name for h in catalogue()}


def _summary_sql(
    where: str = "", params: dict[str, Any] | None = None, limit: int = 100, offset: int = 0
) -> tuple[str, dict[str, Any]]:
    sql = f"""
    SELECT a.id, a.external_id, a.number, a.street, a.unit, a.city, a.postcode, a.lat, a.lon, a.source_id, a.county_fips, r.name AS county,
           fh.classification AS wildfire_fhsz, fl.details->>'fld_zone' AS flood_zone, eq.value AS pga,
           cs.score AS composite_score
    FROM addresses a
    LEFT JOIN regions r ON r.fips = a.county_fips
    LEFT JOIN property_hazard_assessments fh ON fh.address_id = a.id AND fh.hazard_id = 'wildfire_fhsz'
    LEFT JOIN property_hazard_assessments fl ON fl.address_id = a.id AND fl.hazard_id = 'flood_nfhl'
    LEFT JOIN property_hazard_assessments eq ON eq.address_id = a.id AND eq.hazard_id = 'earthquake_pga'
    LEFT JOIN LATERAL (SELECT (details->>'composite_score')::float AS score FROM property_hazard_assessments c WHERE c.address_id = a.id AND c.hazard_id = '_composite' LIMIT 1) cs ON true
    {where}
    ORDER BY a.id LIMIT :limit OFFSET :offset
    """
    p = dict(params or {})
    p.update({"limit": limit, "offset": offset})
    return sql, p


def _row_to_summary(r: Any) -> PropertySummaryOut:
    parts = [p for p in (r.number, r.street, r.unit) if p]
    return PropertySummaryOut(
        id=r.id,
        external_id=r.external_id,
        address=" ".join(parts) or None,
        city=r.city,
        postcode=r.postcode,
        county=r.county,
        county_fips=r.county_fips,
        lat=r.lat,
        lon=r.lon,
        source_id=r.source_id,
        composite_score=r.composite_score,
        wildfire_fhsz=r.wildfire_fhsz,
        flood_zone=r.flood_zone,
        pga_2pc50_g=r.pga,
    )


@router.get("", response_model=PagedProperties)
def list_properties(
    db: DB,
    q: str | None = Query(None, description="address text or numeric id"),
    county: str | None = None,
    bbox: BBox = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> PagedProperties:
    settings = get_settings()
    limit = min(limit, settings.max_page_size)
    conds, params = [], {}
    if q:
        if q.strip().isdigit():
            conds.append("a.id = :qid")
            params["qid"] = int(q)
        else:
            conds.append(
                "(a.number || ' ' || coalesce(a.street,'') || ' ' || coalesce(a.city,'') || ' ' || coalesce(a.postcode,'')) ILIKE :q"
            )
            params["q"] = f"%{q.strip()}%"
    if county:
        conds.append("(a.county_fips = :county OR r.name ILIKE :county_name)")
        params["county"] = county
        params["county_name"] = county
    bb = parse_bbox(bbox)
    if bb:
        conds.append("a.geom && ST_MakeEnvelope(:minx,:miny,:maxx,:maxy,4326)")
        params.update(dict(zip(("minx", "miny", "maxx", "maxy"), bb, strict=True)))
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    sql, p = _summary_sql(where, params, limit, offset)
    rows = db.execute(text(sql), p).all()
    count_sql = (
        f"SELECT count(*) FROM addresses a LEFT JOIN regions r ON r.fips = a.county_fips {where}"
    )
    total = db.execute(text(count_sql), params).scalar_one()
    return PagedProperties(
        items=[_row_to_summary(r) for r in rows], total=int(total), limit=limit, offset=offset
    )


@router.get("/counties")
def counties(db: DB) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Region.name, Region.fips, func.count(Address.id))
        .join(Address, Address.county_fips == Region.fips)
        .group_by(Region.name, Region.fips)
        .order_by(Region.name)
    ).all()
    return [{"county": n, "county_fips": f, "n": int(c)} for n, f, c in rows]


@router.get("/geojson")
def properties_geojson(
    db: DB, bbox: BBox = None, limit: int = Query(5000, ge=1, le=20000), county: str | None = None
) -> dict[str, Any]:
    bb = parse_bbox(bbox)
    if bb is None and county is None:
        raise HTTPException(422, "bbox or county is required")
    conds, params = [], {}
    if bb:
        conds.append("a.geom && ST_MakeEnvelope(:minx,:miny,:maxx,:maxy,4326)")
        params.update(dict(zip(("minx", "miny", "maxx", "maxy"), bb, strict=True)))
    if county:
        conds.append("a.county_fips = :county")
        params["county"] = county
    sql, p = _summary_sql("WHERE " + " AND ".join(conds), params, limit, 0)
    rows = db.execute(text(sql), p).all()
    feats = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
            "properties": {
                "id": r.id,
                "address": " ".join(x for x in (r.number, r.street) if x),
                "county": r.county,
                "composite_score": r.composite_score,
                "wildfire_fhsz": r.wildfire_fhsz,
                "flood_zone": r.flood_zone,
                "pga_2pc50_g": r.pga,
            },
        }
        for r in rows
    ]
    return {"type": "FeatureCollection", "features": feats, "truncated": len(rows) >= limit}


def _hazard_out(a: PropertyHazardAssessment) -> HazardAssessmentOut:
    return HazardAssessmentOut(
        hazard_id=a.hazard_id,
        hazard_name=HAZARD_NAMES.get(a.hazard_id, a.hazard_id),
        source_id=a.source_id,
        source_version=a.source_version,
        assessed_at=a.assessed_at,
        classification=a.classification,
        value=a.value,
        units=a.units,
        probability_horizon=a.probability_horizon,
        coverage_status=a.coverage_status,
        confidence=a.confidence,
        method=a.method,
        details=a.details or {},
        caveats=list(a.caveats or []),
    )  # type: ignore[arg-type]


def _as_result(a: PropertyHazardAssessment) -> HazardResult:
    return HazardResult(
        hazard_id=a.hazard_id,
        hazard_name=HAZARD_NAMES.get(a.hazard_id, a.hazard_id),
        source_id=a.source_id,
        coverage_status=a.coverage_status,
        source_version=a.source_version,
        classification=a.classification,
        value=a.value,
        units=a.units,
        probability_horizon=a.probability_horizon,
        confidence=a.confidence,
        method=a.method,
        details=a.details or {},
        caveats=list(a.caveats or []),
    )  # type: ignore[arg-type]


def _prediction_out(db: DB, address_id: int) -> WildfirePredictionOut | None:
    row = db.execute(
        select(Prediction, ModelVersion)
        .join(ModelVersion, ModelVersion.id == Prediction.model_version_id)
        .where(Prediction.address_id == address_id, ModelVersion.is_selected.is_(True))
    ).first()
    if row is None:
        return None
    pred, mv = row
    thr = float((mv.metrics or {}).get("threshold", 0.5))
    return WildfirePredictionOut(
        model_version=mv.version,
        target_definition=mv.target_definition,
        probability=pred.probability,
        prediction_year=pred.prediction_year,
        threshold=thr,
        predicted_class=pred.probability >= thr,
        base_value=pred.base_value,
        features=pred.features or {},
        shap=(pred.shap_values or {}).get("contributions", []),
        caveats=[
            "Calibrated probability of mapped perimeter exposure next year; not building damage.",
            "SHAP contributions are in the model's raw output space (log-odds for XGBoost/logistic regression).",
        ],
    )


def _structure(
    db: DB, property_id: int
) -> tuple[BuildingProfile | None, StructureAttributesOut | None]:
    link = db.get(AddressStructure, property_id)
    if link is None or link.structure is None:
        return None, None
    st = link.structure
    profile = BuildingProfile(
        bldgtype=st.bldgtype,
        occtype=st.occtype,
        num_story=st.num_story,
        year_built=st.year_built,
        found_type=st.found_type,
        found_ht_ft=st.found_ht_ft,
        ground_elev_ft=st.ground_elev_ft,
        sqft=st.sqft,
        val_struct_usd=st.val_struct_usd,
        damage_category=st.damage_category,
    )
    out = StructureAttributesOut(
        structure_id=st.id,
        bldgtype=st.bldgtype,
        hazus_type=hazus_building_type(profile),
        occtype=st.occtype,
        damage_category=st.damage_category,
        num_story=st.num_story,
        sqft=st.sqft,
        found_type=st.found_type,
        found_ht_ft=st.found_ht_ft,
        ground_elev_ft=st.ground_elev_ft,
        year_built=st.year_built,
        val_struct_usd=st.val_struct_usd,
        firmzone=st.firmzone,
        match_distance_m=link.match_distance_m,
        uncertainty=link.uncertainty,
    )
    return profile, out


@router.get("/{property_id}", response_model=PropertyDetailOut)
def get_property(property_id: int, db: DB) -> PropertyDetailOut:
    sql, p = _summary_sql("WHERE a.id = :pid", {"pid": property_id}, 1, 0)
    row = db.execute(text(sql), p).first()
    if row is None:
        raise HTTPException(404, "property not found")
    summary = _row_to_summary(row)
    addr = db.get(Address, property_id)
    assert addr is not None
    assessments = (
        db.execute(
            select(PropertyHazardAssessment)
            .where(
                PropertyHazardAssessment.address_id == property_id,
                PropertyHazardAssessment.hazard_id != "_composite",
            )
            .order_by(PropertyHazardAssessment.hazard_id)
        )
        .scalars()
        .all()
    )
    results = {a.hazard_id: _as_result(a) for a in assessments}
    pred = _prediction_out(db, property_id)
    if pred and pred.probability is not None:
        results["wildfire_ml"] = HazardResult(
            "wildfire_ml",
            "Wildfire – ML perimeter exposure (next year)",
            pred.model_version,
            "covered",
            value=pred.probability,
            units="probability",
            probability_horizon="next calendar year",
            method="ml_model",
        )
    comp = compute_composite(results) if results else None
    ab = db.get(AddressBuilding, property_id)
    bld = db.get(Building, ab.building_id) if ab and ab.building_id else None
    env = db.get(EnvironmentalObservation, property_id)
    profile, structure_out = _structure(db, property_id)
    fin = compute_scenarios(results, building=profile)
    return PropertyDetailOut(
        **summary.model_dump(),
        acquired_at=addr.acquired_at,
        building=BuildingMatchOut(
            building_id=ab.building_id,
            footprint_area_m2=bld.footprint_area_m2 if bld else None,
            match_method=ab.match_method,
            match_distance_m=ab.match_distance_m,
            buildings_within_100m=ab.buildings_within_100m,
            uncertainty=ab.uncertainty,
            structure=structure_out,
        )
        if ab
        else (BuildingMatchOut(structure=structure_out) if structure_out else None),
        environment=EnvironmentOut(
            elevation_m=env.elevation_m,
            slope_deg=env.slope_deg,
            aspect_deg=env.aspect_deg,
            land_cover_class=env.land_cover_class,
            fuel_model=env.fuel_model,
            distance_to_fault_m=env.distance_to_fault_m,
            distance_to_watercourse_m=env.distance_to_watercourse_m,
            fires_within_5km_since_1990=env.fires_within_5km_since_1990,
            years_since_last_fire_within_1km=env.years_since_last_fire_within_1km,
            sources=env.sources or {},
        )
        if env
        else None,
        hazards=[_hazard_out(a) for a in assessments],
        prediction=pred,
        score=CompositeScoreOut(**comp.to_dict()) if comp else None,
        coverage={a.hazard_id: a.coverage_status for a in assessments},  # type: ignore[misc]
        financial=FinancialScenarioSetOut(
            **{k: v for k, v in fin.items() if k != "assumptions_version"}
        )
        if fin["scenarios"]
        else None,
        composite_score=comp.score if comp else summary.composite_score,
    )


@router.get("/{property_id}/hazards", response_model=list[HazardAssessmentOut])
def get_property_hazards(property_id: int, db: DB) -> list[HazardAssessmentOut]:
    if db.get(Address, property_id) is None:
        raise HTTPException(404, "property not found")
    rows = (
        db.execute(
            select(PropertyHazardAssessment).where(
                PropertyHazardAssessment.address_id == property_id,
                PropertyHazardAssessment.hazard_id != "_composite",
            )
        )
        .scalars()
        .all()
    )
    return [_hazard_out(a) for a in rows]


@router.get("/{property_id}/predictions", response_model=WildfirePredictionOut)
def get_property_prediction(property_id: int, db: DB) -> WildfirePredictionOut:
    if db.get(Address, property_id) is None:
        raise HTTPException(404, "property not found")
    pred = _prediction_out(db, property_id)
    if pred is None:
        raise HTTPException(
            404, "no prediction available (model not trained or predictions not generated)"
        )
    return pred


@router.get("/{property_id}/financial", response_model=FinancialScenarioSetOut)
def get_property_financial(
    property_id: int,
    db: DB,
    insured_value: float | None = Query(None, ge=0),
    property_value: float | None = Query(None, ge=0),
    deductible: float | None = Query(None, ge=0),
    limit: float | None = Query(None, ge=0),
) -> FinancialScenarioSetOut:
    rows = (
        db.execute(
            select(PropertyHazardAssessment).where(
                PropertyHazardAssessment.address_id == property_id,
                PropertyHazardAssessment.hazard_id != "_composite",
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise HTTPException(404, "property not found or not assessed")
    profile, _ = _structure(db, property_id)
    fin = compute_scenarios(
        {a.hazard_id: _as_result(a) for a in rows},
        building=profile,
        insured_value=insured_value,
        property_value=property_value,
        deductible=deductible,
        limit=limit,
    )
    return FinancialScenarioSetOut(**{k: v for k, v in fin.items() if k != "assumptions_version"})


def _unused(_: pd.DataFrame) -> None:  # keeps pandas import for type checkers in some configs
    return None

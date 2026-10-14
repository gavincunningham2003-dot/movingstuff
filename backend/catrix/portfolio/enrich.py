"""Portfolio enrichment job: validate → county assignment → hazards → score → finance."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import geopandas as gpd
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from catrix.config import GEOGRAPHIC_CRS, get_settings
from catrix.db.models import Portfolio, PortfolioProperty, ProcessingJob, Region
from catrix.finance.scenarios import compute_scenarios
from catrix.finance.vulnerability import BuildingProfile
from catrix.geo.spatial import point_in_polygon_join, points_gdf
from catrix.hazards.base import HazardResult
from catrix.hazards.lookup import nearest_structure
from catrix.hazards.registry import assess_points, build_modules
from catrix.ingest.store import rows
from catrix.logging import get_logger
from catrix.ml.predict import predict_for_points
from catrix.portfolio.analytics import summarise_portfolio
from catrix.portfolio.validation import parse_portfolio_csv
from catrix.scoring.composite import compute_composite

log = get_logger(__name__)


def county_names(session: Session) -> dict[str, str]:
    return {fips: name for fips, name in session.execute(select(Region.fips, Region.name)).all()}


def create_portfolio(
    session: Session, content: bytes, name: str | None
) -> tuple[Portfolio, ProcessingJob]:
    df, report = parse_portfolio_csv(content)
    pf = Portfolio(
        id=uuid.uuid4(),
        name=name,
        status="pending",
        n_rows=report.n_rows,
        n_valid=report.n_valid,
        n_invalid=report.n_invalid,
        n_duplicates=report.n_duplicates,
        validation_errors=[*report.errors, *({"row": None, "error": w} for w in report.warnings)],
    )
    job = ProcessingJob(
        id=uuid.uuid4(),
        job_type="portfolio_enrich",
        status="pending",
        params={"portfolio_id": str(pf.id)},
    )
    pf.job_id = job.id
    session.add_all([pf, job])
    session.flush()  # bulk_insert_mappings does not autoflush: parents must exist first
    valid = df[df["valid"]]
    session.bulk_insert_mappings(
        PortfolioProperty,
        [
            {
                "portfolio_id": pf.id,
                "row_number": int(r.row_number),
                "property_id": r.property_id,
                "policy_id": r.policy_id or None,
                "address_text": r.address_text or None,
                "lat": float(r.lat),
                "lon": float(r.lon),
                "property_value": None if pd.isna(r.property_value) else float(r.property_value),
                "insured_value": None if pd.isna(r.insured_value) else float(r.insured_value),
                "is_duplicate_of": None
                if r.is_duplicate_of is None or pd.isna(r.is_duplicate_of)
                else int(r.is_duplicate_of),
                "financial": {
                    "deductible": None
                    if r.deductible is None or pd.isna(r.deductible)
                    else float(r.deductible)
                },
                "geom": f"SRID=4326;POINT({float(r.lon)} {float(r.lat)})",
            }
            for r in rows(valid)
        ],
    )
    session.flush()
    return pf, job


def claim_job(session: Session, job_id: uuid.UUID) -> bool:
    """Move one specific job from 'pending' to 'claimed' atomically; False if another
    runner got there first (or the job is not pending)."""
    from sqlalchemy import text

    row = session.execute(
        text(
            "UPDATE processing_jobs SET status = 'claimed' "
            "WHERE id = :id AND status = 'pending' RETURNING id"
        ),
        {"id": job_id},
    ).first()
    session.commit()
    return row is not None


def run_portfolio_job(session: Session, job_id: uuid.UUID, *, allow_network: bool = True) -> None:
    job = session.get(ProcessingJob, job_id)
    assert job is not None
    assert job.params is not None
    pf = session.get(Portfolio, uuid.UUID(job.params["portfolio_id"]))
    assert pf is not None
    job.status = pf.status = "running"
    job.started_at = datetime.now(UTC)
    session.commit()
    try:
        _enrich(session, pf, job, allow_network=allow_network)
        job.status = pf.status = "completed"
        job.progress = 1.0
    except Exception as exc:
        log.exception("portfolio_job_failed", job=str(job_id))
        job.status = pf.status = "failed"
        job.error = pf.error = f"{type(exc).__name__}: {exc}"[:2000]
    job.finished_at = datetime.now(UTC)
    session.commit()


def _enrich(session: Session, pf: Portfolio, job: ProcessingJob, *, allow_network: bool) -> None:
    settings = get_settings()
    props = (
        session.execute(
            select(PortfolioProperty)
            .where(PortfolioProperty.portfolio_id == pf.id)
            .order_by(PortfolioProperty.row_number)
        )
        .scalars()
        .all()
    )
    if not props:
        pf.analysis = summarise_portfolio(
            pd.DataFrame(
                columns=[
                    "property_id",
                    "lat",
                    "lon",
                    "county_fips",
                    "insured_value",
                    "property_value",
                    "is_duplicate_of",
                    "composite_score",
                    "hazards",
                    "financial",
                ]
            ),
            {},
        )
        return
    df = pd.DataFrame(
        [
            {
                "pk": p.id,
                "property_id": p.property_id,
                "lat": p.lat,
                "lon": p.lon,
                "insured_value": p.insured_value,
                "property_value": p.property_value,
                "is_duplicate_of": p.is_duplicate_of,
                "deductible": (p.financial or {}).get("deductible"),
            }
            for p in props
        ]
    )
    regions = gpd.read_postgis(
        select(Region.fips, Region.geom), session.connection(), geom_col="geom", crs=GEOGRAPHIC_CRS
    ).rename_geometry("geometry")
    df["county_fips"] = (
        point_in_polygon_join(points_gdf(df), regions, ["fips"])["fips"].values
        if len(regions)
        else None
    )
    modules = build_modules(allow_network=allow_network)
    names = county_names(session)
    hazards_col: list[dict[str, Any]] = []
    scores: list[float | None] = []
    fin_col: list[dict[str, Any] | None] = []
    bs = settings.batch_size
    for start in range(0, len(df), bs):
        chunk = df.iloc[start : start + bs]
        res = assess_points(session, chunk[["lon", "lat", "county_fips"]], modules)
        preds = predict_for_points(session, chunk[["lon", "lat", "county_fips"]])
        structures = nearest_structure(session, chunk[["lon", "lat"]])
        for j in range(len(chunk)):
            per: dict[str, HazardResult] = {hid: lst[j] for hid, lst in res.items()}
            pj = preds[j] if preds else None
            if pj is not None and pj.get("probability") is not None:
                per["wildfire_ml"] = HazardResult(
                    "wildfire_ml",
                    "Wildfire – ML perimeter exposure (next year)",
                    pj["model_version"],
                    "covered",
                    value=pj["probability"],
                    units="probability",
                    probability_horizon="next calendar year",
                    method="ml_model",
                    confidence="medium",
                    details={"threshold": pj.get("threshold")},
                    caveats=pj.get("caveats", []),
                )
            comp = compute_composite(per)
            row = chunk.iloc[j]
            st = structures.get(int(chunk.index[j]))
            building = (
                BuildingProfile(
                    bldgtype=st["bldgtype"],
                    occtype=st["occtype"],
                    num_story=st["num_story"],
                    year_built=st["year_built"],
                    found_type=st["found_type"],
                    found_ht_ft=st["found_ht_ft"],
                    ground_elev_ft=st["ground_elev_ft"],
                    sqft=st["sqft"],
                    val_struct_usd=st["val_struct_usd"],
                    damage_category=st["damage_category"],
                )
                if st
                else None
            )
            fin = compute_scenarios(
                per,
                building=building,
                insured_value=row["insured_value"] if pd.notna(row["insured_value"]) else None,
                property_value=row["property_value"] if pd.notna(row["property_value"]) else None,
                deductible=row["deductible"]
                if row["deductible"] is not None and pd.notna(row["deductible"])
                else None,
            )
            hazards_col.append({hid: r.to_dict() for hid, r in per.items()})
            scores.append(comp.score)
            fin_col.append({**fin, "composite": comp.to_dict()})
        job.progress = min(0.9, (start + len(chunk)) / len(df))
        session.commit()
    df["hazards"] = pd.Series(hazards_col, index=df.index, dtype=object)
    df["composite_score"] = pd.Series(scores, index=df.index, dtype=float)
    df["financial"] = pd.Series(fin_col, index=df.index, dtype=object)
    for p, r in zip(props, rows(df), strict=True):
        p.county_fips = r.county_fips if isinstance(r.county_fips, str) else None
        p.hazards = r.hazards
        p.composite_score = r.composite_score
        p.financial = r.financial
    pf.analysis = summarise_portfolio(df, names)
    session.flush()

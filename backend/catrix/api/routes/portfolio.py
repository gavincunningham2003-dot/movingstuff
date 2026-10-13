from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from sqlalchemy import func, select, text

from catrix.api.deps import DB
from catrix.api.schemas import (
    PortfolioAnalysisOut,
    PortfolioStatusOut,
    PortfolioSummaryOut,
    PortfolioUploadResponse,
)
from catrix.config import get_settings
from catrix.db.models import (
    Address,
    HazardSource,
    ModelVersion,
    Portfolio,
    PropertyHazardAssessment,
    Region,
)
from catrix.db.session import session_scope
from catrix.portfolio.enrich import create_portfolio, run_portfolio_job
from catrix.portfolio.export import export_rows, to_csv, to_json

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _run_job(job_id: uuid.UUID) -> None:
    with session_scope() as s:
        run_portfolio_job(s, job_id)


def _status(pf: Portfolio) -> PortfolioStatusOut:
    progress = 1.0 if pf.status == "completed" else 0.0
    return PortfolioStatusOut(
        portfolio_id=str(pf.id),
        name=pf.name,
        status=pf.status,
        created_at=pf.created_at,
        n_rows=pf.n_rows,
        n_valid=pf.n_valid,
        n_invalid=pf.n_invalid,
        n_duplicates=pf.n_duplicates,
        validation_errors=pf.validation_errors or [],
        progress=progress,
        error=pf.error,
    )  # type: ignore[arg-type]


@router.get("/summary", response_model=PortfolioSummaryOut)
def base_summary(db: DB) -> PortfolioSummaryOut:
    """Executive overview of the ingested base dataset (not an uploaded portfolio)."""
    total = int(db.execute(select(func.count(Address.id))).scalar_one())
    county_rows = db.execute(
        text(
            """
            SELECT r.name, r.fips, count(a.id) AS n,
                   avg((c.details->>'composite_score')::float) AS mean_score,
                   count(*) FILTER (WHERE fh.classification = 'Very High') AS n_vh,
                   count(*) FILTER (WHERE (fl.details->>'sfha') = 'true') AS n_sfha,
                   avg(eq.value) AS mean_pga
            FROM addresses a JOIN regions r ON r.fips = a.county_fips
            LEFT JOIN property_hazard_assessments fh ON fh.address_id = a.id AND fh.hazard_id = 'wildfire_fhsz'
            LEFT JOIN property_hazard_assessments fl ON fl.address_id = a.id AND fl.hazard_id = 'flood_nfhl'
            LEFT JOIN property_hazard_assessments eq ON eq.address_id = a.id AND eq.hazard_id = 'earthquake_pga'
            LEFT JOIN property_hazard_assessments c ON c.address_id = a.id AND c.hazard_id = '_composite'
            GROUP BY r.name, r.fips ORDER BY n DESC
            """
        )
    ).all()
    counties = [
        {
            "county": n,
            "county_fips": f,
            "n_properties": int(c),
            "share_pct": round(100.0 * c / total, 1) if total else 0.0,
            "total_insured_value": None,
            "mean_composite_score": ms,
            "n_fhsz_very_high": int(vh),
            "n_sfha": int(sf),
            "mean_pga_2pc50_g": mp,
        }
        for n, f, c, ms, vh, sf, mp in county_rows
    ]
    dist_rows = db.execute(
        text(
            "SELECT hazard_id, coverage_status, coalesce(classification, coverage_status) AS cls, count(*) FROM property_hazard_assessments WHERE hazard_id <> '_composite' GROUP BY 1,2,3"
        )
    ).all()
    hazard_distribution: dict[str, dict[str, int]] = {}
    coverage: dict[str, dict[str, int]] = {}
    for hid, cov, cls, n in dist_rows:
        coverage.setdefault(hid, {})
        coverage[hid][cov] = coverage[hid].get(cov, 0) + int(n)
        if cov in ("covered", "not_in_hazard_area"):
            hazard_distribution.setdefault(hid, {})
            key = cls if cov == "covered" else "not in hazard area"
            hazard_distribution[hid][key] = hazard_distribution[hid].get(key, 0) + int(n)
    mv = db.execute(
        select(ModelVersion).where(ModelVersion.is_selected.is_(True))
    ).scalar_one_or_none()
    model = None
    if mv:
        t = (mv.metrics or {}).get("test", {}).get("metrics", {})
        model = {
            "version": mv.version,
            "algorithm": mv.algorithm,
            "test_period": mv.test_period,
            "roc_auc": t.get("roc_auc"),
            "pr_auc": t.get("pr_auc"),
            "brier": t.get("brier"),
            "prevalence": t.get("prevalence"),
            "n_test": t.get("n"),
        }
    n_sources = db.execute(
        select(func.count(HazardSource.id)).where(HazardSource.ingestion_status == "ingested")
    ).scalar_one()
    notes = [
        "Composite values are a 0-100 screening index with documented, assumed weights; they are not probabilities or premiums.",
        "Hazard distributions count authoritative classifications per hazard; different hazards are on different scales and are never summed.",
        "No property values are attached to the base dataset; monetary exposure appears only for uploaded portfolios that supply values.",
        f"{int(n_sources)} data sources currently ingested; see Data Quality for coverage and unavailable sources.",
    ]
    return PortfolioSummaryOut(
        total_properties=total,
        counties=counties,
        hazard_distribution=hazard_distribution,
        coverage=coverage,
        exposure_value={"available": False, "note": "Base dataset carries no monetary values."},
        model=model,
        interpretation_notes=notes,
        generated_at=datetime.now(UTC),
    )  # type: ignore[arg-type]


@router.get("/example.csv")
def example_csv() -> Response:
    p = Path(get_settings().data_dir) / "samples" / "example_portfolio.csv"
    if not p.exists():
        raise HTTPException(404, "example portfolio not found")
    return FileResponse(p, media_type="text/csv", filename="example_portfolio.csv")


@router.get("", response_model=list[PortfolioStatusOut])
def list_portfolios(db: DB, limit: int = Query(50, ge=1, le=200)) -> list[PortfolioStatusOut]:
    rows = (
        db.execute(select(Portfolio).order_by(Portfolio.created_at.desc()).limit(limit))
        .scalars()
        .all()
    )
    return [_status(p) for p in rows]


@router.post("/upload", response_model=PortfolioUploadResponse, status_code=202)
async def upload_portfolio(
    db: DB, background: BackgroundTasks, file: UploadFile = File(...), name: str | None = Form(None)
) -> PortfolioUploadResponse:
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file exceeds 25 MB")
    try:
        pf, job = create_portfolio(db, content, name or file.filename)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    background.add_task(_run_job, job.id)
    return PortfolioUploadResponse(
        portfolio_id=str(pf.id), job_id=str(job.id), status=pf.status, n_rows_received=pf.n_rows
    )


def _get(db: DB, portfolio_id: str) -> Portfolio:
    try:
        pid = uuid.UUID(portfolio_id)
    except ValueError as exc:
        raise HTTPException(422, "invalid portfolio id") from exc
    pf = db.get(Portfolio, pid)
    if pf is None:
        raise HTTPException(404, "portfolio not found")
    return pf


@router.get("/{portfolio_id}", response_model=PortfolioStatusOut)
def portfolio_status(portfolio_id: str, db: DB) -> PortfolioStatusOut:
    pf = _get(db, portfolio_id)
    out = _status(pf)
    if pf.job_id and pf.status == "running":
        from catrix.db.models import ProcessingJob

        job = db.get(ProcessingJob, pf.job_id)
        out.progress = job.progress if job else 0.0
    return out


@router.get("/{portfolio_id}/analysis", response_model=PortfolioAnalysisOut)
def portfolio_analysis(portfolio_id: str, db: DB) -> PortfolioAnalysisOut:
    pf = _get(db, portfolio_id)
    if pf.status != "completed" or not pf.analysis:
        raise HTTPException(409, f"portfolio status is '{pf.status}'; analysis not available")
    a: dict[str, Any] = pf.analysis
    return PortfolioAnalysisOut(
        portfolio_id=str(pf.id),
        generated_at=datetime.fromisoformat(a["generated_at"]),
        summary=a["summary"],
        hazard_distribution=a["hazard_distribution"],
        coverage=a["coverage"],
        by_county=a["by_county"],
        concentration=a["concentration"],
        top_exposures=a["top_exposures"],
        financial=a.get("financial"),
        warnings=a.get("warnings", []),
    )


@router.get("/{portfolio_id}/export")
def portfolio_export(
    portfolio_id: str, db: DB, format: str = Query("csv", pattern="^(csv|json)$")
) -> Response:
    pf = _get(db, portfolio_id)
    if pf.status != "completed":
        raise HTTPException(409, f"portfolio status is '{pf.status}'")
    rows = export_rows(db, pf)
    if format == "csv":
        return PlainTextResponse(
            to_csv(rows),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="catrix_portfolio_{pf.id}.csv"'},
        )
    return Response(
        to_json(pf, rows),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="catrix_portfolio_{pf.id}.json"'},
    )


def _unused(_: Region, __: PropertyHazardAssessment) -> None:
    return None

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import select, text

from catrix.api.deps import DB
from catrix.api.schemas import DataQualityOut, DatasetQualityOut, QualityCheckOut
from catrix.db.models import HazardSource, ModelVersion
from catrix.ingest.registry import load_registry
from catrix.quality.checks import latest_reports

router = APIRouter(tags=["meta"])


@router.get("/data-quality", response_model=DataQualityOut)
def data_quality(db: DB) -> DataQualityOut:
    reg = load_registry()
    state = {r.id: r for r in db.execute(select(HazardSource)).scalars().all()}
    cov = {
        hid: (n_cov, n_tot)
        for hid, n_cov, n_tot in db.execute(
            text(
                "SELECT hazard_id, count(*) FILTER (WHERE coverage_status IN ('covered','not_in_hazard_area')), count(*) FROM property_hazard_assessments GROUP BY hazard_id"
            )
        ).all()
    }
    source_to_hazard = {
        "calfire_fhsz_2024": "wildfire_fhsz",
        "calfire_fire_perimeters": "wildfire_history",
        "cgs_ms48_pga_2pc50": "earthquake_pga",
        "fema_nfhl": "flood_nfhl",
        "cgs_landslide_susceptibility_ms58": "landslide",
        "cgs_seismic_hazard_zones": "liquefaction",
        "cgs_tsunami_hazard_area": "tsunami",
        "noaa_atlas14": "extreme_rainfall",
        "cgs_qfaults": "earthquake_fault_proximity",
        "usgs_earthquake_catalog": "earthquake_history",
    }
    datasets = []
    unavailable = []
    for s in reg.sources:
        row = state.get(s.id)
        status = row.ingestion_status if row else "never_ingested"
        if status in ("unavailable", "failed") or (
            status == "never_ingested" and s.verification_status != "verified"
        ):
            unavailable.append(s.id)
        c = cov.get(source_to_hazard.get(s.id, ""))
        coverage_pct = round(100.0 * c[0] / c[1], 1) if c and c[1] else None
        datasets.append(
            DatasetQualityOut(
                source_id=s.id,
                name=s.name,
                verification_status=s.verification_status,
                ingestion_status=status,
                last_ingested_at=row.retrieved_at if row else None,
                record_count=row.record_count if row else None,
                version=row.version if row else None,
                coverage_pct=coverage_pct,
                missing_rate_pct=(
                    round(100.0 - coverage_pct, 1) if coverage_pct is not None else None
                ),
                errors=[row.error] if row and row.error else [],
            )
        )
    checks = [
        QualityCheckOut(
            name=r.check_name,
            status=r.status,
            n_checked=r.n_checked,
            n_failed=r.n_failed,
            detail=(", ".join(f"{k}={v}" for k, v in (r.detail or {}).items()) or None),
        )
        for r in latest_reports(db)
    ]  # type: ignore[arg-type]
    errors = [
        {
            "source_id": r.id,
            "error": r.error,
            "at": r.retrieved_at.isoformat() if r.retrieved_at else None,
        }
        for r in state.values()
        if r.error
    ]
    mv = db.execute(
        select(ModelVersion.version).where(ModelVersion.is_selected.is_(True))
    ).scalar_one_or_none()
    return DataQualityOut(
        generated_at=datetime.now(UTC),
        datasets=datasets,
        checks=checks,
        processing_errors=errors,
        unavailable_sources=unavailable,
        model_version=mv,
        note="All layers are batch snapshots taken at the recorded ingestion time; nothing is refreshed in real time.",
    )

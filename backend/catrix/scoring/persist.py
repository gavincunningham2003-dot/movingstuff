"""Compute and persist the composite screening index for every ingested address.

The score is stored as a pseudo-assessment row (``hazard_id = '_composite'``) so the
list/summary queries can read it with a single join. It is recomputed whenever hazards or
predictions change.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from catrix.db.models import ModelVersion, Prediction, PropertyHazardAssessment
from catrix.hazards.base import HazardResult
from catrix.logging import get_logger
from catrix.scoring.composite import METHOD_VERSION, compute_composite

log = get_logger(__name__)


def score_all_addresses(session: Session, batch: int = 5000) -> int:
    session.execute(
        delete(PropertyHazardAssessment).where(PropertyHazardAssessment.hazard_id == "_composite")
    )
    session.flush()
    mv = session.execute(
        select(ModelVersion.id).where(ModelVersion.is_selected.is_(True))
    ).scalar_one_or_none()
    preds: dict[int, float] = {}
    if mv is not None:
        preds = {
            aid: p
            for aid, p in session.execute(
                select(Prediction.address_id, Prediction.probability).where(
                    Prediction.model_version_id == mv
                )
            ).all()
        }
    rows = session.execute(
        select(PropertyHazardAssessment)
        .where(PropertyHazardAssessment.hazard_id != "_composite")
        .order_by(PropertyHazardAssessment.address_id)
    ).scalars()
    current: int | None = None
    per: dict[str, HazardResult] = {}
    inserts = []
    n = 0

    def flush_one(aid: int) -> None:
        nonlocal inserts
        if aid in preds:
            per["wildfire_ml"] = HazardResult(
                "wildfire_ml",
                "Wildfire – ML",
                "ml",
                "covered",
                value=preds[aid],
                units="probability",
                method="ml_model",
            )
        comp = compute_composite(per)
        inserts.append(
            {
                "address_id": aid,
                "hazard_id": "_composite",
                "source_id": "calfire_fhsz_2024",
                "source_version": METHOD_VERSION,
                "assessed_at": datetime.now(UTC),
                "classification": comp.label,
                "value": comp.score,
                "units": "screening index 0-100",
                "coverage_status": "covered" if comp.score is not None else "unavailable",
                "confidence": "low",
                "method": "static_overlay",
                "details": {"composite_score": comp.score, **comp.to_dict()},
                "caveats": [comp.weights_note],
            }
        )

    for a in rows:
        if current is not None and a.address_id != current:
            flush_one(current)
            per = {}
            n += 1
            if len(inserts) >= batch:
                session.bulk_insert_mappings(PropertyHazardAssessment, inserts)
                session.commit()
                inserts = []
        current = a.address_id
        per[a.hazard_id] = HazardResult(
            a.hazard_id,
            a.hazard_id,
            a.source_id,
            a.coverage_status,
            classification=a.classification,
            value=a.value,
            units=a.units,
            details=a.details or {},
        )  # type: ignore[arg-type]
    if current is not None:
        flush_one(current)
        n += 1
    if inserts:
        session.bulk_insert_mappings(PropertyHazardAssessment, inserts)
    session.commit()
    log.info("composite_scored", n=n)
    return n

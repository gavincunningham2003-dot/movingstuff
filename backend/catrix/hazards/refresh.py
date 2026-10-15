"""Versioned, atomic refresh of ``property_hazard_assessments``.

A refresh writes its rows to ``assessment_staging`` under a new ``assessment_runs`` id,
committing batch by batch so progress survives a crash of the writer. Nothing live
changes until :func:`commit_run`, which in **one transaction** deletes the live rows of the
refreshed hazards, moves the staged rows in and marks the run completed. Readers therefore
see either the previous assessment or the new one, never a mixture or an empty table, and a
failed run (:func:`fail_run`) leaves the previous assessment untouched.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from catrix.db.models import AssessmentRun, AssessmentStaging, PropertyHazardAssessment
from catrix.ingest.registry import ensure_sources
from catrix.logging import get_logger

log = get_logger(__name__)

_COLUMNS = (
    "address_id",
    "hazard_id",
    "source_id",
    "source_version",
    "assessed_at",
    "classification",
    "value",
    "units",
    "probability_horizon",
    "coverage_status",
    "confidence",
    "method",
    "details",
    "caveats",
)


def begin_run(
    session: Session,
    *,
    hazard_ids: list[str],
    kind: str = "hazards",
    n_addresses: int = 0,
    notes: str | None = None,
) -> AssessmentRun:
    run = AssessmentRun(
        id=uuid.uuid4(),
        status="running",
        kind=kind,
        hazard_ids=list(hazard_ids),
        started_at=datetime.now(UTC),
        n_addresses=n_addresses,
        notes=notes,
    )
    session.add(run)
    session.commit()
    log.info("assessment_run_started", run=str(run.id), kind=kind, hazards=hazard_ids)
    return run


def stage_rows(session: Session, run: AssessmentRun, rows: list[dict[str, Any]]) -> None:
    """Append a batch to the staging table and commit (progress is durable)."""
    if not rows:
        return
    ensure_sources(session, {str(r["source_id"]) for r in rows})
    session.bulk_insert_mappings(
        AssessmentStaging, [{"run_id": run.id, **{c: r.get(c) for c in _COLUMNS}} for r in rows]
    )
    session.commit()


def commit_run(session: Session, run: AssessmentRun, counts: dict[str, int] | None = None) -> int:
    """Atomically replace the live rows of the run's hazards with the staged rows."""
    hazard_ids = list(run.hazard_ids or [])
    cols = ", ".join(_COLUMNS)
    with session.begin_nested():
        session.execute(
            text("DELETE FROM property_hazard_assessments WHERE hazard_id = ANY(:hids)"),
            {"hids": hazard_ids},
        )
        inserted = session.execute(
            text(
                f"INSERT INTO property_hazard_assessments ({cols}, run_id) "
                f"SELECT {cols}, run_id FROM assessment_staging WHERE run_id = :rid"
            ),
            {"rid": run.id},
        )
        moved = int(getattr(inserted, "rowcount", 0) or 0)
        session.execute(text("DELETE FROM assessment_staging WHERE run_id = :rid"), {"rid": run.id})
        run.status = "completed"
        run.finished_at = datetime.now(UTC)
        run.counts = counts or {"rows": int(moved)}
    session.commit()
    log.info("assessment_run_committed", run=str(run.id), rows=int(moved))
    return int(moved)


def fail_run(session: Session, run: AssessmentRun, error: str) -> None:
    """Record the failure and discard staged rows; the live table is untouched."""
    session.rollback()
    run = session.merge(run)
    session.execute(text("DELETE FROM assessment_staging WHERE run_id = :rid"), {"rid": run.id})
    run.status = "failed"
    run.finished_at = datetime.now(UTC)
    run.error = error[:2000]
    session.commit()
    log.warning("assessment_run_failed", run=str(run.id), error=error[:200])


def list_runs(session: Session, limit: int = 20) -> list[AssessmentRun]:
    return list(
        session.execute(
            select(AssessmentRun).order_by(AssessmentRun.started_at.desc()).limit(limit)
        ).scalars()
    )


def current_run_ids(session: Session) -> dict[str, str | None]:
    """hazard_id -> run id of the live rows (None for rows written before versioning)."""
    rows = session.execute(
        select(PropertyHazardAssessment.hazard_id, PropertyHazardAssessment.run_id).distinct()
    ).all()
    return {h: (str(r) if r else None) for h, r in rows}

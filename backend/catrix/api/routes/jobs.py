from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from catrix.api.deps import DB
from catrix.api.schemas import AssessmentRunOut, JobOut
from catrix.db.models import ProcessingJob
from catrix.hazards.refresh import list_runs

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/assessment-runs", response_model=list[AssessmentRunOut])
def assessment_runs(db: DB, limit: int = 20) -> list[AssessmentRunOut]:
    """Versioned assessment refreshes, newest first (status, hazards, counts, timing)."""
    return [
        AssessmentRunOut(
            run_id=str(r.id),
            status=r.status,
            kind=r.kind,
            hazard_ids=list(r.hazard_ids or []),
            started_at=r.started_at,
            finished_at=r.finished_at,
            n_addresses=r.n_addresses,
            counts=r.counts,
            error=r.error,
        )
        for r in list_runs(db, limit=max(1, min(limit, 200)))
    ]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: DB) -> JobOut:
    try:
        jid = uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(422, "invalid job id") from exc
    job = db.get(ProcessingJob, jid)
    if job is None:
        raise HTTPException(404, "job not found")
    return JobOut(
        job_id=str(job.id),
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        result=job.result,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        lease_expires_at=job.lease_expires_at,
        heartbeat_at=job.heartbeat_at,
        worker_id=job.worker_id,
        last_error=job.last_error,
    )

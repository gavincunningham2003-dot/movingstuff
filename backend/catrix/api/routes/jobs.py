from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from catrix.api.deps import DB
from catrix.api.schemas import JobOut
from catrix.db.models import ProcessingJob

router = APIRouter(prefix="/jobs", tags=["jobs"])


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
    )

"""Leased, retryable processing jobs.

A job is claimed with an atomic ``UPDATE ... WHERE`` that succeeds for a ``pending`` job or
for a ``claimed``/``running`` job whose lease has expired (its runner died) and that still
has attempts left. The runner extends the lease with :func:`heartbeat` as it makes
progress; :func:`recover_stale_jobs` (run by the worker on every poll and by the API at
startup) returns expired jobs to ``pending`` or marks them ``failed`` once their attempts
are exhausted. Both the API's inline runner and the database worker use these functions,
so the two can never run the same job at the same time.
"""

from __future__ import annotations

import socket
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from catrix.db.models import ProcessingJob
from catrix.logging import get_logger

log = get_logger(__name__)

DEFAULT_LEASE_SECONDS = 600
DEFAULT_MAX_ATTEMPTS = 3

_CLAIMABLE = (
    "(status = 'pending' OR (status IN ('claimed', 'running') "
    "AND lease_expires_at IS NOT NULL AND lease_expires_at < now()))"
)


def worker_identity() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"


def claim_job(session: Session, job_id: uuid.UUID, worker_id: str | None = None) -> bool:
    """Claim one specific job; False when another runner holds a live lease on it, it is
    not pending, or its attempts are exhausted."""
    row = session.execute(
        text(
            f"""
            UPDATE processing_jobs
               SET status = 'claimed',
                   attempts = attempts + 1,
                   worker_id = :wid,
                   heartbeat_at = now(),
                   lease_expires_at = now() + make_interval(secs => lease_seconds)
             WHERE id = :id AND attempts < max_attempts AND {_CLAIMABLE}
             RETURNING id
            """
        ),
        {"id": job_id, "wid": worker_id or worker_identity()},
    ).first()
    session.commit()
    return row is not None


def claim_next(session: Session, worker_id: str | None = None) -> uuid.UUID | None:
    """Claim the oldest claimable job (``FOR UPDATE SKIP LOCKED`` keeps workers apart)."""
    row = session.execute(
        text(
            f"""
            UPDATE processing_jobs
               SET status = 'claimed',
                   attempts = attempts + 1,
                   worker_id = :wid,
                   heartbeat_at = now(),
                   lease_expires_at = now() + make_interval(secs => lease_seconds)
             WHERE id = (
                   SELECT id FROM processing_jobs
                    WHERE attempts < max_attempts AND {_CLAIMABLE}
                    ORDER BY created_at
                    LIMIT 1 FOR UPDATE SKIP LOCKED)
             RETURNING id
            """
        ),
        {"wid": worker_id or worker_identity()},
    ).first()
    session.commit()
    return row[0] if row else None


def heartbeat(session: Session, job: ProcessingJob, progress: float | None = None) -> None:
    """Extend the lease (and record progress); call it at every durable step."""
    now = datetime.now(UTC)
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=job.lease_seconds or DEFAULT_LEASE_SECONDS)
    if progress is not None:
        job.progress = progress


def recover_stale_jobs(session: Session) -> dict[str, int]:
    """Expired leases: back to ``pending`` while attempts remain, else ``failed``."""
    retried = session.execute(
        text(
            """
            UPDATE processing_jobs
               SET status = 'pending',
                   last_error = coalesce(last_error, 'runner lost (lease expired)'),
                   worker_id = NULL, lease_expires_at = NULL
             WHERE status IN ('claimed', 'running')
               AND lease_expires_at IS NOT NULL AND lease_expires_at < now()
               AND attempts < max_attempts
             RETURNING id
            """
        )
    ).all()
    failed = session.execute(
        text(
            """
            UPDATE processing_jobs
               SET status = 'failed',
                   error = coalesce(error, 'runner lost (lease expired) and no attempts left'),
                   finished_at = now()
             WHERE status IN ('claimed', 'running')
               AND lease_expires_at IS NOT NULL AND lease_expires_at < now()
               AND attempts >= max_attempts
             RETURNING id, params
            """
        )
    ).all()
    for _jid, params in failed:
        pid = (params or {}).get("portfolio_id")
        if pid:
            session.execute(
                text(
                    "UPDATE portfolios SET status = 'failed', "
                    "error = coalesce(error, 'processing job lost and not retried') "
                    "WHERE id = CAST(:pid AS uuid) AND status IN ('pending', 'running')"
                ),
                {"pid": pid},
            )
    session.commit()
    out = {"retried": len(retried), "failed": len(failed)}
    if retried or failed:
        log.warning("stale_jobs_recovered", **out)
    return out

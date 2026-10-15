"""Minimal database-polling background worker for ``processing_jobs``.

The API runs portfolio jobs in-process via FastAPI ``BackgroundTasks`` by default; this
worker exists for the Docker Compose deployment so long jobs do not share the API process.
It polls for claimable jobs (``pending``, or an expired lease with attempts left),
claims one with ``FOR UPDATE SKIP LOCKED``, heartbeats while it runs and sweeps stale
leases on every poll (see :mod:`catrix.portfolio.jobs`).
"""

from __future__ import annotations

import time
import uuid

from catrix.config import get_settings
from catrix.db.session import session_scope
from catrix.logging import configure_logging, get_logger
from catrix.portfolio.enrich import run_portfolio_job
from catrix.portfolio.jobs import claim_next, recover_stale_jobs, worker_identity

log = get_logger(__name__)
POLL_SECONDS = 3.0


def claim_job(worker_id: str | None = None) -> uuid.UUID | None:
    with session_scope() as s:
        recover_stale_jobs(s)
        return claim_next(s, worker_id)


def main() -> None:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    wid = worker_identity()
    log.info("worker_started", worker=wid)
    while True:
        jid = claim_job(wid)
        if jid is None:
            time.sleep(POLL_SECONDS)
            continue
        log.info("job_claimed", job=str(jid), worker=wid)
        with session_scope() as session:
            run_portfolio_job(session, jid)


if __name__ == "__main__":
    main()

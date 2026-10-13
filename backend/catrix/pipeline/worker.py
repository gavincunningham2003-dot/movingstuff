"""Minimal database-polling background worker for ``processing_jobs``.

The API runs portfolio jobs in-process via FastAPI ``BackgroundTasks`` by default; this
worker exists for the Docker Compose deployment so long jobs do not share the API process.
It polls for ``pending`` jobs, claims one with ``FOR UPDATE SKIP LOCKED`` and runs it.
"""

from __future__ import annotations

import time
import uuid

from sqlalchemy import text

from catrix.config import get_settings
from catrix.db.session import session_scope
from catrix.logging import configure_logging, get_logger
from catrix.portfolio.enrich import run_portfolio_job

log = get_logger(__name__)
POLL_SECONDS = 3.0


def claim_job() -> uuid.UUID | None:
    with session_scope() as s:
        row = s.execute(
            text(
                """
                UPDATE processing_jobs SET status = 'claimed'
                WHERE id = (SELECT id FROM processing_jobs WHERE status = 'pending' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
                RETURNING id, job_type
                """
            )
        ).first()
        return row[0] if row else None


def main() -> None:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    log.info("worker_started")
    while True:
        jid = claim_job()
        if jid is None:
            time.sleep(POLL_SECONDS)
            continue
        log.info("job_claimed", job=str(jid))
        with session_scope() as session:
            run_portfolio_job(session, jid)


if __name__ == "__main__":
    main()

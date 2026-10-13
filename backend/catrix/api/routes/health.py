from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from catrix import __version__
from catrix.api.schemas import HealthResponse
from catrix.db.session import database_available

router = APIRouter(tags=["meta"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    ok, detail = database_available()
    return HealthResponse(
        status="ok" if ok else "degraded",
        version=__version__,
        database="ok" if ok else "unavailable",
        timestamp=datetime.now(UTC),
        detail=detail,
    )

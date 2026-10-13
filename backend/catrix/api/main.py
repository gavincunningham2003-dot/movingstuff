"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from catrix import __version__
from catrix.api.routes import (
    data_quality,
    data_sources,
    hazards,
    health,
    jobs,
    models,
    portfolio,
    properties,
)
from catrix.config import get_settings
from catrix.logging import configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    settings.ensure_dirs()
    log.info("api_started", version=__version__, env=settings.env)
    yield
    log.info("api_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="CATRIX — California Catastrophe Risk Intelligence Platform",
        version=__version__,
        description=(
            "Property-level multi-hazard exposure, wildfire perimeter-exposure ML, portfolio "
            "analytics and data provenance for California. Screening scores are not probabilities "
            "or premiums; every hazard carries its own units and coverage status."
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    prefix = "/api/v1"
    for r in (health, data_sources, properties, hazards, portfolio, models, data_quality, jobs):
        app.include_router(r.router, prefix=prefix)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=str(request.url))
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"{type(exc).__name__}: {exc}",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    return app


app = create_app()

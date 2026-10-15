"""Shared FastAPI dependencies."""

from __future__ import annotations

import hmac
from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from catrix.config import get_settings
from catrix.db.session import get_sessionmaker


def db_session() -> Generator[Session, None, None]:
    try:
        session = get_sessionmaker()()
    except OperationalError as exc:  # pragma: no cover - env specific
        raise HTTPException(503, f"database unavailable: {exc}") from exc
    try:
        yield session
    except OperationalError as exc:
        raise HTTPException(503, f"database unavailable: {str(exc).splitlines()[0]}") from exc
    finally:
        session.close()


DB = Annotated[Session, Depends(db_session)]


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """No-op unless ``CATRIX_API_KEY`` is configured; then every request on the protected
    routers must carry a matching ``X-API-Key`` header (constant-time comparison)."""
    expected = get_settings().api_key
    if not expected:
        return
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or invalid API key (X-API-Key)")


APIKey = Depends(require_api_key)


def parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    """``minLon,minLat,maxLon,maxLat`` with an area cap to prevent unbounded queries."""
    if not bbox:
        return None
    try:
        parts = [float(v) for v in bbox.split(",")]
    except ValueError as exc:
        raise HTTPException(422, "bbox must be minLon,minLat,maxLon,maxLat") from exc
    if len(parts) != 4:
        raise HTTPException(422, "bbox must have four numbers")
    minx, miny, maxx, maxy = parts
    if not (-180 <= minx < maxx <= 180 and -90 <= miny < maxy <= 90):
        raise HTTPException(422, "bbox out of range")
    area = (maxx - minx) * (maxy - miny)
    if area > get_settings().max_bbox_area_deg2:
        raise HTTPException(
            422,
            f"bbox area {area:.2f} deg^2 exceeds the {get_settings().max_bbox_area_deg2} deg^2 limit; zoom in",
        )
    return (minx, miny, maxx, maxy)


BBox = Annotated[str | None, Query(description="minLon,minLat,maxLon,maxLat (WGS84)")]

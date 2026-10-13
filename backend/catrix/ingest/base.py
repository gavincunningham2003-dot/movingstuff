"""Connector protocol and result type."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session


@dataclass
class IngestResult:
    source_id: str
    status: str  # 'ingested' | 'unavailable' | 'failed' | 'skipped'
    record_count: int = 0
    version: str | None = None
    message: str | None = None
    quality: dict[str, Any] = field(default_factory=dict)


class Connector(Protocol):
    source_id: str

    def run(self, session: Session, *, force: bool = False, **kwargs: Any) -> IngestResult: ...

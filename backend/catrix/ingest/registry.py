"""Loads ``data_sources.yaml`` and syncs it with the ``hazard_sources`` table."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from catrix.config import get_settings
from catrix.db.models import HazardSource


class SourceSpec(BaseModel):
    id: str
    name: str
    provider: str
    category: str
    url: str | None = None
    api_url: str | None = None
    download_url: str | None = None
    download_pattern: str | None = None
    format: str | None = None
    crs: str | None = None
    geographic_coverage: str | None = None
    temporal_coverage: str | None = None
    licence: str | None = None
    auth_required: bool = False
    verified_on: str | None = None
    verification_status: Literal["verified", "unverified", "local_only"]
    fields: list[str] = Field(default_factory=list)
    notes: str | None = None

    @property
    def raw_dir(self) -> Path:
        return get_settings().raw_dir / self.id


class Registry(BaseModel):
    registry_version: str
    sources: list[SourceSpec]

    def get(self, source_id: str) -> SourceSpec:
        for s in self.sources:
            if s.id == source_id:
                return s
        raise KeyError(f"unknown data source '{source_id}'")

    def ids(self) -> list[str]:
        return [s.id for s in self.sources]


@lru_cache(maxsize=1)
def load_registry(path: Path | None = None) -> Registry:
    p = path or get_settings().registry_path
    if not p.exists():
        raise FileNotFoundError(f"data_sources.yaml not found at {p}")
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return Registry.model_validate(data)


def sync_registry(session: Session, registry: Registry | None = None) -> int:
    """Upsert registry entries into ``hazard_sources`` without touching ingestion state."""
    reg = registry or load_registry()
    n = 0
    for spec in reg.sources:
        row = session.get(HazardSource, spec.id)
        if row is None:
            row = HazardSource(
                id=spec.id,
                name=spec.name,
                provider=spec.provider,
                source_url=spec.api_url or spec.download_url or spec.url,
                ingestion_status="never_ingested",
            )
            session.add(row)
            n += 1
        else:
            row.name = spec.name
            row.provider = spec.provider
            row.source_url = spec.api_url or spec.download_url or spec.url
    session.flush()
    return n


def mark_ingested(
    session: Session,
    source_id: str,
    *,
    record_count: int,
    version: str | None = None,
    checksum: str | None = None,
    extra: dict | None = None,
) -> None:
    row = session.get(HazardSource, source_id)
    if row is None:
        spec = load_registry().get(source_id)
        row = HazardSource(id=spec.id, name=spec.name, provider=spec.provider)
        session.add(row)
    row.retrieved_at = datetime.now(UTC)
    row.record_count = record_count
    row.version = version
    row.checksum = checksum
    row.ingestion_status = "ingested"
    row.error = None
    row.extra = extra
    session.flush()


def mark_failed(session: Session, source_id: str, error: str, *, unavailable: bool = False) -> None:
    row = session.get(HazardSource, source_id)
    if row is None:
        spec = load_registry().get(source_id)
        row = HazardSource(id=spec.id, name=spec.name, provider=spec.provider)
        session.add(row)
    row.ingestion_status = "unavailable" if unavailable else "failed"
    row.error = error[:4000]
    session.flush()

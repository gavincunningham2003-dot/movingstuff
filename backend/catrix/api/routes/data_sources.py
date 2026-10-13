from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from catrix.api.deps import DB
from catrix.api.schemas import DataSourceOut
from catrix.db.models import HazardSource
from catrix.ingest.registry import load_registry

router = APIRouter(tags=["meta"])


@router.get("/data-sources", response_model=list[DataSourceOut])
def list_data_sources(db: DB) -> list[DataSourceOut]:
    reg = load_registry()
    state = {r.id: r for r in db.execute(select(HazardSource)).scalars().all()}
    out = []
    for s in reg.sources:
        row = state.get(s.id)
        out.append(
            DataSourceOut(
                **s.model_dump(exclude={"fields"}),
                last_ingested_at=row.retrieved_at if row else None,
                record_count=row.record_count if row else None,
                version=row.version if row else None,
                ingestion_status=(row.ingestion_status if row else "never_ingested"),  # type: ignore[arg-type]
            )
        )
    return out

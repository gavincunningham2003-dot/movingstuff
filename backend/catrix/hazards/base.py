"""Common output contract for every hazard module.

A module never raises for a single point: it returns a :class:`HazardResult` whose
``coverage_status`` says *why* a value may be missing:

* ``covered``             – the location is inside a mapped hazard area / has a value.
* ``not_in_hazard_area``  – the source covers the location and reports no hazard zone.
* ``outside_coverage``    – the source does not map this location (unmapped ≠ low risk).
* ``unavailable``         – the source could not be ingested or queried.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import pandas as pd
from sqlalchemy.orm import Session

CoverageStatus = Literal["covered", "not_in_hazard_area", "outside_coverage", "unavailable"]
Method = Literal["static_overlay", "raster_sample", "point_service", "ml_model", "regional_index"]
Confidence = Literal["high", "medium", "low"]


@dataclass
class HazardResult:
    hazard_id: str
    hazard_name: str
    source_id: str
    coverage_status: CoverageStatus
    source_version: str | None = None
    assessed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    classification: str | None = None
    value: float | None = None
    units: str | None = None
    probability_horizon: str | None = None
    confidence: Literal["high", "medium", "low"] | None = None
    method: Method | None = None
    details: dict[str, Any] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["assessed_at"] = self.assessed_at.isoformat()
        return d


@dataclass(frozen=True)
class HazardInfo:
    hazard_id: str
    name: str
    description: str
    source_ids: tuple[str, ...]
    method: Method
    units: str | None
    probability_horizon: str | None
    scale_note: str
    map_layer_available: bool = False


class HazardModule(ABC):
    info: HazardInfo

    @abstractmethod
    def assess(self, session: Session, points: pd.DataFrame) -> list[HazardResult]:
        """``points`` has columns ``lon``, ``lat`` and optionally ``county_fips``; the
        result list is aligned with ``points`` row order."""

    # Helper used by every module when the source could not be loaded.
    def unavailable(self, n: int, reason: str, source_id: str | None = None) -> list[HazardResult]:
        return [
            HazardResult(
                hazard_id=self.info.hazard_id,
                hazard_name=self.info.name,
                source_id=source_id or self.info.source_ids[0],
                coverage_status="unavailable",
                method=self.info.method,
                units=self.info.units,
                probability_horizon=self.info.probability_horizon,
                caveats=[reason],
            )
            for _ in range(n)
        ]

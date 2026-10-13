"""Hazard module registry and batch assessment runner."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from catrix.config import get_settings
from catrix.db.models import Address, PropertyHazardAssessment
from catrix.hazards.base import HazardInfo, HazardModule, HazardResult
from catrix.hazards.earthquake import (
    EarthquakeHistoryModule,
    EarthquakePGA10pcModule,
    EarthquakePGAModule,
    FaultProximityModule,
)
from catrix.hazards.flood import FloodNFHLModule
from catrix.hazards.secondary import (
    ExtremeRainfallModule,
    LandslideSusceptibilityModule,
    LiquefactionModule,
    RegionalIndexModule,
    TsunamiModule,
)
from catrix.hazards.wildfire import WildfireFHSZModule, WildfirePerimeterHistoryModule
from catrix.logging import get_logger

log = get_logger(__name__)


def build_modules(*, allow_network: bool = True) -> list[HazardModule]:
    return [
        WildfireFHSZModule(),
        WildfirePerimeterHistoryModule(),
        EarthquakePGAModule(allow_network=allow_network),
        EarthquakePGA10pcModule(allow_network=allow_network),
        FaultProximityModule(),
        EarthquakeHistoryModule(),
        FloodNFHLModule(),
        LandslideSusceptibilityModule(allow_network=allow_network),
        LiquefactionModule(),
        TsunamiModule(),
        ExtremeRainfallModule(allow_network=allow_network),
        RegionalIndexModule(
            "windstorm",
            "Windstorm – county NRI benchmark",
            "windstorm",
            "FEMA NRI strong-wind risk score for the county.",
        ),
        RegionalIndexModule(
            "drought",
            "Drought – county NRI benchmark",
            "drought",
            "FEMA NRI drought risk score for the county.",
        ),
        RegionalIndexModule(
            "extreme_heat",
            "Extreme heat – county NRI benchmark",
            "extreme_heat",
            "FEMA NRI heat-wave risk score for the county.",
        ),
        RegionalIndexModule(
            "coastal_flooding",
            "Coastal flooding – county NRI benchmark",
            "coastal_flooding",
            "FEMA NRI coastal-flooding risk score for the county (sea-level-rise scenarios are not represented).",
        ),
        RegionalIndexModule(
            "winter_storm",
            "Winter storm – county NRI benchmark",
            "winter_storm",
            "FEMA NRI winter-weather risk score for the county.",
        ),
    ]


def catalogue(modules: Iterable[HazardModule] | None = None) -> list[HazardInfo]:
    return [m.info for m in (modules or build_modules(allow_network=False))]


def assess_points(
    session: Session, points: pd.DataFrame, modules: Iterable[HazardModule] | None = None
) -> dict[str, list[HazardResult]]:
    """Run every module over ``points`` (columns lon, lat[, county_fips])."""
    results: dict[str, list[HazardResult]] = {}
    for m in modules or build_modules():
        try:
            results[m.info.hazard_id] = m.assess(session, points)
        except Exception as exc:
            log.exception("hazard_module_failed", hazard=m.info.hazard_id)
            results[m.info.hazard_id] = m.unavailable(len(points), f"module error: {exc!r}")
        n_cov = sum(1 for r in results[m.info.hazard_id] if r.coverage_status == "covered")
        log.info("hazard_assessed", hazard=m.info.hazard_id, n=len(points), covered=n_cov)
    return results


def assess_all_addresses(
    session: Session, *, hazard_ids: list[str] | None = None, batch_size: int | None = None
) -> dict[str, int]:
    """Assess every ingested address and persist to ``property_hazard_assessments``."""
    settings = get_settings()
    bs = batch_size or settings.batch_size
    rows = session.execute(
        select(Address.id, Address.lon, Address.lat, Address.county_fips).order_by(Address.id)
    ).all()
    modules = [m for m in build_modules() if not hazard_ids or m.info.hazard_id in hazard_ids]
    counts: dict[str, int] = {m.info.hazard_id: 0 for m in modules}
    for m in modules:
        session.execute(
            delete(PropertyHazardAssessment).where(
                PropertyHazardAssessment.hazard_id == m.info.hazard_id
            )
        )
    for start in range(0, len(rows), bs):
        chunk = rows[start : start + bs]
        df = pd.DataFrame(chunk, columns=["address_id", "lon", "lat", "county_fips"])
        res = assess_points(session, df, modules)
        inserts = []
        for hid, lst in res.items():
            for aid, r in zip(df["address_id"], lst, strict=True):
                inserts.append(
                    {
                        "address_id": int(aid),
                        "hazard_id": hid,
                        "source_id": r.source_id,
                        "source_version": r.source_version,
                        "assessed_at": r.assessed_at,
                        "classification": r.classification,
                        "value": r.value,
                        "units": r.units,
                        "probability_horizon": r.probability_horizon,
                        "coverage_status": r.coverage_status,
                        "confidence": r.confidence,
                        "method": r.method,
                        "details": r.details,
                        "caveats": r.caveats,
                    }
                )
                counts[hid] += 1
        session.bulk_insert_mappings(PropertyHazardAssessment, inserts)
        session.commit()
        log.info(
            "assessment_batch_committed",
            done=min(start + bs, len(rows)),
            total=len(rows),
            at=datetime.now(UTC).isoformat(),
        )
    return counts

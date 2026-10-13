"""Data-quality checks run after ingestion and surfaced on the Data Quality page."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from catrix.config import CALIFORNIA_BBOX
from catrix.db.models import DataQualityReport


@dataclass
class CheckResult:
    name: str
    status: str  # pass | warn | fail | skipped
    n_checked: int = 0
    n_failed: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


def _scalar(session: Session, sql: str, **params: Any) -> Any:
    return session.execute(text(sql), params).scalar()


def run_checks(session: Session) -> list[CheckResult]:
    results: list[CheckResult] = []
    n_addr = int(_scalar(session, "SELECT count(*) FROM addresses") or 0)
    if n_addr == 0:
        results.append(
            CheckResult("addresses_present", "fail", 0, 0, {"message": "no addresses ingested"})
        )
        return results
    results.append(CheckResult("addresses_present", "pass", n_addr, 0))

    minx, miny, maxx, maxy = CALIFORNIA_BBOX
    n_out = int(
        _scalar(
            session,
            "SELECT count(*) FROM addresses WHERE lon < :minx OR lon > :maxx OR lat < :miny OR lat > :maxy",
            minx=minx,
            maxx=maxx,
            miny=miny,
            maxy=maxy,
        )
        or 0
    )
    results.append(
        CheckResult(
            "coordinates_within_california_bbox", "pass" if n_out == 0 else "fail", n_addr, n_out
        )
    )

    n_nocounty = int(
        _scalar(session, "SELECT count(*) FROM addresses WHERE county_fips IS NULL") or 0
    )
    results.append(
        CheckResult(
            "addresses_have_county", "pass" if n_nocounty == 0 else "warn", n_addr, n_nocounty
        )
    )

    n_dup = int(_scalar(session, "SELECT count(*) - count(DISTINCT dedup_key) FROM addresses") or 0)
    results.append(
        CheckResult("duplicate_locations", "pass" if n_dup == 0 else "warn", n_addr, n_dup)
    )

    n_invalid_geom = int(
        _scalar(session, "SELECT count(*) FROM hazard_geometries WHERE NOT ST_IsValid(geom)") or 0
    )
    n_geom = int(_scalar(session, "SELECT count(*) FROM hazard_geometries") or 0)
    results.append(
        CheckResult(
            "hazard_geometries_valid",
            "pass" if n_invalid_geom == 0 else "warn",
            n_geom,
            n_invalid_geom,
        )
        if n_geom
        else CheckResult("hazard_geometries_valid", "skipped")
    )

    n_fire = int(_scalar(session, "SELECT count(*) FROM wildfire_events") or 0)
    if n_fire:
        n_noyear = int(
            _scalar(session, "SELECT count(*) FROM wildfire_events WHERE year IS NULL") or 0
        )
        results.append(
            CheckResult(
                "wildfire_events_have_year", "pass" if n_noyear == 0 else "warn", n_fire, n_noyear
            )
        )
        n_future = int(
            _scalar(
                session,
                "SELECT count(*) FROM wildfire_events WHERE year > :y",
                y=datetime.now(UTC).year,
            )
            or 0
        )
        results.append(
            CheckResult(
                "wildfire_events_no_future_years",
                "pass" if n_future == 0 else "fail",
                n_fire,
                n_future,
            )
        )
    else:
        results.append(CheckResult("wildfire_events_have_year", "skipped"))

    # Hazard coverage per module
    for hid, status_counts in _coverage(session).items():
        total = sum(status_counts.values())
        unavailable = status_counts.get("unavailable", 0)
        results.append(
            CheckResult(
                f"coverage_{hid}",
                "pass" if unavailable == 0 else ("warn" if unavailable < total else "fail"),
                total,
                unavailable,
                status_counts,
            )
        )

    # Environmental feature ranges
    n_env = int(_scalar(session, "SELECT count(*) FROM environmental_observations") or 0)
    if n_env:
        n_bad = int(
            _scalar(
                session,
                "SELECT count(*) FROM environmental_observations WHERE elevation_m < -100 OR elevation_m > 4500 OR slope_deg < 0 OR slope_deg > 90 OR aspect_deg > 360",
            )
            or 0
        )
        n_null = int(
            _scalar(
                session, "SELECT count(*) FROM environmental_observations WHERE elevation_m IS NULL"
            )
            or 0
        )
        results.append(
            CheckResult("terrain_values_in_range", "pass" if n_bad == 0 else "fail", n_env, n_bad)
        )
        results.append(
            CheckResult(
                "terrain_missing_elevation",
                "pass" if n_null == 0 else "warn",
                n_addr,
                n_addr - n_env + n_null,
            )
        )
        n_unmatched = int(
            _scalar(session, "SELECT count(*) FROM address_buildings WHERE match_method = 'none'")
            or 0
        )
        n_ab = int(_scalar(session, "SELECT count(*) FROM address_buildings") or 0)
        results.append(
            CheckResult(
                "building_match_rate",
                "pass" if n_ab and n_unmatched / n_ab < 0.2 else "warn",
                n_ab,
                n_unmatched,
            )
            if n_ab
            else CheckResult("building_match_rate", "skipped")
        )
    else:
        results.append(CheckResult("terrain_values_in_range", "skipped"))
    return results


def _coverage(session: Session) -> dict[str, dict[str, int]]:
    rows = session.execute(
        text(
            "SELECT hazard_id, coverage_status, count(*) AS n FROM property_hazard_assessments GROUP BY 1,2"
        )
    ).all()
    out: dict[str, dict[str, int]] = {}
    for hid, st, n in rows:
        out.setdefault(hid, {})[st] = int(n)
    return out


def persist(session: Session, stage: str, results: list[CheckResult]) -> None:
    for r in results:
        session.add(
            DataQualityReport(
                stage=stage,
                check_name=r.name,
                status=r.status,
                n_checked=r.n_checked,
                n_failed=r.n_failed,
                detail=r.detail,
            )
        )
    session.flush()


def latest_reports(session: Session) -> list[DataQualityReport]:
    sql = text(
        """
        SELECT DISTINCT ON (check_name) id FROM data_quality_reports ORDER BY check_name, created_at DESC
        """
    )
    ids = [r[0] for r in session.execute(sql).all()]
    if not ids:
        return []
    return list(
        session.query(DataQualityReport)
        .filter(DataQualityReport.id.in_(ids))
        .order_by(DataQualityReport.check_name)
        .all()
    )

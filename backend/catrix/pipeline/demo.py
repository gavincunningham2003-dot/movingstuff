"""Reproducible demo: upload the bundled example portfolio, enrich it against whatever is
ingested, and print what an underwriter would look at first.

Runs without network lookups by default (``allow_network=False``: point services and
raster downloads are skipped, so the result depends only on the local database). With
an empty database it still completes: every hazard is reported as unavailable, the
duplicate/validation counts and the money totals are real, and the stress-test list is
empty. This is the fixture the end-to-end tests and the CI demo job use.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from catrix.config import get_settings
from catrix.db.models import Portfolio
from catrix.portfolio.enrich import claim_job, create_portfolio, run_portfolio_job
from catrix.portfolio.stress import list_events, portfolio_totals


def example_csv_path() -> Path:
    """The bundled example: under the configured data directory, else the repository copy
    (tests point ``CATRIX_DATA_DIR`` at an empty temporary directory)."""
    configured = Path(get_settings().data_dir) / "samples" / "example_portfolio.csv"
    if configured.exists():
        return configured
    return Path(__file__).resolve().parents[3] / "data" / "samples" / "example_portfolio.csv"


def run_demo(
    session: Session,
    *,
    csv_path: Path | None = None,
    name: str = "demo",
    allow_network: bool = False,
) -> dict[str, Any]:
    path = csv_path or example_csv_path()
    content = path.read_bytes()
    pf, job = create_portfolio(session, content, name)
    session.commit()
    assert claim_job(session, job.id, "demo")
    run_portfolio_job(session, job.id, allow_network=allow_network)
    refreshed = session.get(Portfolio, pf.id)
    assert refreshed is not None
    pf = refreshed
    analysis = pf.analysis or {}
    events = list_events(session, str(pf.id)) if pf.status == "completed" else []
    out: dict[str, Any] = {
        "portfolio_id": str(pf.id),
        "status": pf.status,
        "error": pf.error,
        "rows": {
            "received": pf.n_rows,
            "valid": pf.n_valid,
            "invalid": pf.n_invalid,
            "exact_duplicates_excluded": pf.n_duplicates,
            "shared_location": pf.n_shared_location,
            "distinct_locations": pf.n_locations,
        },
        "summary": analysis.get("summary"),
        "financial": {
            k: (analysis.get("financial") or {}).get(k)
            for k in (
                "total_insured_value",
                "total_replacement_value",
                "total_policy_limit",
                "n_with_limit",
            )
        },
        "top_exposures": [
            {k: e.get(k) for k in ("property_id", "county", "insured_value", "composite_score")}
            for e in (analysis.get("top_exposures") or [])[:5]
        ],
        "location_accumulation": analysis.get("location_accumulation") or [],
        "stress_events": [
            {
                "fire_name": e.fire_name,
                "year": e.year,
                "n_exposures_inside": e.n_exposures_inside,
                "total_insured_value_inside": e.total_insured_value_inside,
                "share_of_tiv_pct": e.share_of_tiv_pct,
            }
            for e in events[:5]
        ],
        "portfolio_totals": portfolio_totals(session, str(pf.id)),
        "warnings": analysis.get("warnings") or [],
    }
    return out


def format_report(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, default=str)

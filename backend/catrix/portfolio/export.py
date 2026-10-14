"""Enriched CSV / JSON export of a processed portfolio."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from catrix.db.models import Portfolio, PortfolioProperty

EXPORT_HAZARDS = [
    "wildfire_fhsz",
    "wildfire_history",
    "wildfire_ml",
    "earthquake_pga",
    "flood_nfhl",
    "landslide",
    "liquefaction",
    "tsunami",
    "extreme_rainfall",
    "earthquake_fault_proximity",
]


def export_rows(session: Session, pf: Portfolio) -> list[dict[str, Any]]:
    props = (
        session.execute(
            select(PortfolioProperty)
            .where(PortfolioProperty.portfolio_id == pf.id)
            .order_by(PortfolioProperty.row_number)
        )
        .scalars()
        .all()
    )
    rows = []
    for p in props:
        h = p.hazards or {}
        row: dict[str, Any] = {
            "row_number": p.row_number,
            "property_id": p.property_id,
            "policy_id": p.policy_id,
            "address": p.address_text,
            "latitude": p.lat,
            "longitude": p.lon,
            "county_fips": p.county_fips,
            "duplicate_kind": p.duplicate_kind,
            "is_duplicate_of_row": p.is_duplicate_of,
            "location_key": p.location_key,
            "property_value": p.property_value,
            "contents_value": p.contents_value,
            "insured_value": p.insured_value,
            "policy_limit": p.policy_limit,
            "deductible": (
                p.deductible if p.deductible is not None else (p.financial or {}).get("deductible")
            ),
            "exposed_value_basis": (p.financial or {}).get("exposed_value_basis"),
            "composite_screening_index": p.composite_score,
            "composite_label": ((p.financial or {}).get("composite") or {}).get("label"),
        }
        for hid in EXPORT_HAZARDS:
            r = h.get(hid, {})
            row[f"{hid}__class"] = r.get("classification")
            row[f"{hid}__value"] = r.get("value")
            row[f"{hid}__units"] = r.get("units")
            row[f"{hid}__coverage"] = r.get("coverage_status")
            row[f"{hid}__source"] = r.get("source_id")
        for s in (p.financial or {}).get("scenarios", []):
            row[f"scenario__{s['scenario_id']}__gross_loss"] = s.get("gross_loss")
            row[f"scenario__{s['scenario_id']}__net_insured_loss"] = s.get("net_insured_loss")
        rows.append(row)
    return rows


def to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def to_json(pf: Portfolio, rows: list[dict[str, Any]]) -> str:
    return json.dumps(
        {
            "portfolio_id": str(pf.id),
            "name": pf.name,
            "created_at": pf.created_at.isoformat(),
            "analysis": pf.analysis,
            "properties": rows,
        },
        default=str,
        indent=2,
    )

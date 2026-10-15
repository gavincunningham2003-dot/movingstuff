"""Historical catastrophe stress tests.

"What would this portfolio have looked like if the Camp Fire happened again?" The test
overlays an ingested CAL FIRE perimeter on the uploaded exposures with one set-based
PostGIS query (``ST_Intersects`` on the GiST-indexed perimeter geometry, optionally
``ST_DWithin`` on geography for a near-miss buffer) and reports:

* which exposures fall inside the perimeter (and, with a buffer, how close the rest come);
* the sum insured and replacement value hit, as a share of the whole portfolio;
* gross and net loss under a user-chosen **assumed** damage ratio (default
  ``DEFAULT_DAMAGE_RATIO``), applying each exposure's deductible and limit exactly as the
  scenario module does.

What it is not: the perimeter is where the fire burned, not where structures were lost;
survival inside a perimeter varies from a few per cent to near-total between fires, which
is why the damage ratio is an explicit input and every total carries its assumptions.
Results are not expected annual losses and carry no event frequency.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from catrix.finance.scenarios import apply_policy_terms, resolve_exposed_value, resolve_limit
from catrix.portfolio.analytics import EXCLUDED_DUPLICATE_KINDS

DEFAULT_DAMAGE_RATIO = 0.5
MAX_BUFFER_M = 5_000.0
MIN_CATALOGUE_ACRES = 10_000.0
HitKind = Literal["inside", "within_buffer"]

ASSUMPTIONS = [
    "The perimeter marks the burned area, not structure loss: survival inside a perimeter "
    "ranges from a few per cent to near-total between fires, so the damage ratio is an "
    "explicit assumption, not a measurement.",
    "Exposures within the buffer (but outside the perimeter) are reported for context; "
    "they are not included in the loss totals.",
    "Gross loss = exposed (replacement) value x damage ratio; net applies each exposure's "
    "deductible and limit (sum insured when no limit is given). Exposures without a value "
    "are counted as hit but contribute no loss.",
    "This is a single historical footprint replayed on today's portfolio: no frequency, "
    "no expected annual loss, no correlation with other events.",
]


@dataclass
class StressEventSummary:
    event_id: int
    fire_name: str | None
    year: int | None
    gis_acres: float | None
    alarm_date: str | None
    n_exposures_inside: int
    n_exposures_within_buffer: int
    total_insured_value_inside: float | None
    share_of_tiv_pct: float | None
    centroid_lat: float | None
    centroid_lon: float | None


@dataclass
class StressHit:
    property_id: str
    policy_id: str | None
    row_number: int
    lat: float
    lon: float
    county_fips: str | None
    hit_kind: HitKind
    distance_m: float
    insured_value: float | None
    property_value: float | None
    contents_value: float | None
    exposed_value: float | None
    exposed_value_basis: str
    deductible: float | None
    limit: float | None
    gross_loss: float | None
    net_insured_loss: float | None
    composite_score: float | None
    duplicate_kind: str | None


@dataclass
class StressTestResult:
    portfolio_id: str
    event: dict[str, Any]
    damage_ratio: float
    buffer_m: float
    generated_at: str
    totals: dict[str, Any]
    hits: list[StressHit]
    perimeter: dict[str, Any] | None
    assumptions: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _f(v: Any) -> float | None:
    """Float or None; NaN (a pandas artefact in stored scores) is treated as missing."""
    if v is None:
        return None
    x = float(v)
    return None if math.isnan(x) else x


def _excluded_clause() -> str:
    kinds = ", ".join(f"'{k}'" for k in EXCLUDED_DUPLICATE_KINDS)
    return f"(pp.duplicate_kind IS NULL OR pp.duplicate_kind NOT IN ({kinds}))"


def portfolio_totals(session: Session, portfolio_id: str) -> dict[str, Any]:
    row = (
        session.execute(
            text(
                f"""
                SELECT count(*) AS n_exposures,
                       sum(pp.insured_value) AS total_insured_value,
                       sum(coalesce(pp.property_value, 0) + coalesce(pp.contents_value, 0))
                         FILTER (WHERE pp.property_value IS NOT NULL OR pp.contents_value IS NOT NULL)
                         AS total_replacement_value
                FROM portfolio_properties pp
                WHERE pp.portfolio_id = :pid AND {_excluded_clause()}
                """
            ),
            {"pid": portfolio_id},
        )
        .mappings()
        .one()
    )
    return {
        "n_exposures": int(row["n_exposures"] or 0),
        "total_insured_value": None
        if row["total_insured_value"] is None
        else float(row["total_insured_value"]),
        "total_replacement_value": None
        if row["total_replacement_value"] is None
        else float(row["total_replacement_value"]),
    }


def list_events(
    session: Session,
    portfolio_id: str,
    *,
    buffer_m: float = 0.0,
    min_year: int | None = None,
    limit: int = 200,
) -> list[StressEventSummary]:
    """Every ingested perimeter that touches at least one exposure (or comes within
    ``buffer_m`` of one), ranked by sum insured inside, then by exposures inside."""
    buffer_m = max(0.0, min(float(buffer_m), MAX_BUFFER_M))
    totals = portfolio_totals(session, portfolio_id)
    tiv = totals["total_insured_value"]
    sql = text(
        f"""
        WITH pts AS (
            SELECT pp.id, pp.insured_value, pp.geom
            FROM portfolio_properties pp
            WHERE pp.portfolio_id = :pid AND {_excluded_clause()}
        ),
        hits AS (
            SELECT w.id AS event_id,
                   ST_Intersects(w.geom, p.geom) AS inside,
                   p.insured_value
            FROM wildfire_events w
            JOIN pts p
              ON (CAST(:buf AS float) > 0 AND ST_DWithin(w.geom::geography, p.geom::geography, CAST(:buf AS float)))
              OR ST_Intersects(w.geom, p.geom)
            WHERE (CAST(:min_year AS integer) IS NULL OR w.year >= CAST(:min_year AS integer))
        )
        SELECT w.id AS event_id, w.fire_name, w.year, w.gis_acres, w.alarm_date,
               count(*) FILTER (WHERE h.inside) AS n_inside,
               count(*) FILTER (WHERE NOT h.inside) AS n_buffer,
               sum(h.insured_value) FILTER (WHERE h.inside) AS tiv_inside,
               ST_Y(ST_Centroid(w.geom)) AS clat, ST_X(ST_Centroid(w.geom)) AS clon
        FROM hits h JOIN wildfire_events w ON w.id = h.event_id
        GROUP BY w.id, w.fire_name, w.year, w.gis_acres, w.alarm_date, w.geom
        ORDER BY tiv_inside DESC NULLS LAST, n_inside DESC, w.year DESC
        LIMIT :lim
        """
    )
    rows = (
        session.execute(
            sql, {"pid": portfolio_id, "buf": buffer_m, "min_year": min_year, "lim": limit}
        )
        .mappings()
        .all()
    )
    out: list[StressEventSummary] = []
    for r in rows:
        tiv_inside = _f(r["tiv_inside"])
        out.append(
            StressEventSummary(
                event_id=int(r["event_id"]),
                fire_name=(r["fire_name"] or "").strip() or None,
                year=r["year"],
                gis_acres=_f(r["gis_acres"]),
                alarm_date=r["alarm_date"].isoformat() if r["alarm_date"] else None,
                n_exposures_inside=int(r["n_inside"] or 0),
                n_exposures_within_buffer=int(r["n_buffer"] or 0),
                total_insured_value_inside=tiv_inside,
                share_of_tiv_pct=round(100.0 * tiv_inside / tiv, 2)
                if tiv_inside is not None and tiv
                else None,
                centroid_lat=None if r["clat"] is None else round(float(r["clat"]), 5),
                centroid_lon=None if r["clon"] is None else round(float(r["clon"]), 5),
            )
        )
    return out


def catalogue(
    session: Session,
    *,
    min_acres: float = MIN_CATALOGUE_ACRES,
    min_year: int = 2000,
    limit: int = 400,
) -> list[dict[str, Any]]:
    """Large historical fires available for replay regardless of whether they touch the
    portfolio (so a user can pick a famous event and see that it misses)."""
    rows = (
        session.execute(
            text(
                """
                SELECT id, fire_name, year, gis_acres, alarm_date,
                       ST_Y(ST_Centroid(geom)) AS clat, ST_X(ST_Centroid(geom)) AS clon
                FROM wildfire_events
                WHERE gis_acres >= :acres AND year >= :y
                ORDER BY gis_acres DESC
                LIMIT :lim
                """
            ),
            {"acres": min_acres, "y": min_year, "lim": limit},
        )
        .mappings()
        .all()
    )
    return [
        {
            "event_id": int(r["id"]),
            "fire_name": (r["fire_name"] or "").strip() or None,
            "year": r["year"],
            "gis_acres": _f(r["gis_acres"]),
            "alarm_date": r["alarm_date"].isoformat() if r["alarm_date"] else None,
            "centroid_lat": None if r["clat"] is None else round(float(r["clat"]), 5),
            "centroid_lon": None if r["clon"] is None else round(float(r["clon"]), 5),
        }
        for r in rows
    ]


def _event(session: Session, event_id: int, *, with_geometry: bool) -> dict[str, Any] | None:
    cols = "id, fire_name, year, gis_acres, alarm_date, cont_date, agency, inc_num, cause"
    geom = (
        ", ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.0005), 6) AS gj"
        if with_geometry
        else ""
    )
    r = (
        session.execute(
            text(f"SELECT {cols}{geom} FROM wildfire_events WHERE id = :id"), {"id": event_id}
        )
        .mappings()
        .first()
    )
    if r is None:
        return None
    d: dict[str, Any] = {
        "event_id": int(r["id"]),
        "fire_name": (r["fire_name"] or "").strip() or None,
        "year": r["year"],
        "gis_acres": _f(r["gis_acres"]),
        "alarm_date": r["alarm_date"].isoformat() if r["alarm_date"] else None,
        "cont_date": r["cont_date"].isoformat() if r["cont_date"] else None,
        "agency": r["agency"],
        "inc_num": r["inc_num"],
        "cause": r["cause"],
        "source_id": "calfire_fire_perimeters",
    }
    if with_geometry and r["gj"]:
        d["geometry"] = json.loads(r["gj"])
    return d


def hit_loss(
    *,
    damage_ratio: float,
    insured_value: float | None,
    property_value: float | None,
    contents_value: float | None,
    deductible: float | None,
    limit: float | None,
) -> tuple[float | None, str, float | None, float | None, float | None]:
    """(exposed, basis, limit_applied, gross, net) for one hit; pure, so it is unit-tested."""
    exposed, _kind, _basis_text = resolve_exposed_value(
        insured_value, property_value, None, contents_value
    )
    from catrix.finance.scenarios import exposed_value_basis

    basis = exposed_value_basis(insured_value, property_value, None, contents_value)
    lim, _ = resolve_limit(limit, insured_value)
    if exposed is None:
        return None, basis, lim, None, None
    gross = exposed * damage_ratio
    net = (
        apply_policy_terms(gross, deductible, lim)
        if deductible is not None or lim is not None
        else None
    )
    return exposed, basis, lim, round(gross, 2), None if net is None else round(net, 2)


def run_stress_test(
    session: Session,
    portfolio_id: str,
    event_id: int,
    *,
    damage_ratio: float = DEFAULT_DAMAGE_RATIO,
    buffer_m: float = 0.0,
    include_geometry: bool = True,
) -> StressTestResult | None:
    if not 0.0 < damage_ratio <= 1.0:
        raise ValueError("damage_ratio must be in (0, 1]")
    buffer_m = max(0.0, min(float(buffer_m), MAX_BUFFER_M))
    event = _event(session, event_id, with_geometry=include_geometry)
    if event is None:
        return None
    perimeter = event.pop("geometry", None)
    totals_all = portfolio_totals(session, portfolio_id)
    sql = text(
        f"""
        SELECT pp.property_id, pp.policy_id, pp.row_number, pp.lat, pp.lon, pp.county_fips,
               pp.insured_value, pp.property_value, pp.contents_value, pp.policy_limit,
               coalesce(pp.deductible, (pp.financial->>'deductible')::float) AS deductible,
               pp.composite_score, pp.duplicate_kind,
               ST_Intersects(w.geom, pp.geom) AS inside,
               ST_Distance(w.geom::geography, pp.geom::geography) AS distance_m
        FROM portfolio_properties pp
        JOIN wildfire_events w ON w.id = :eid
        WHERE pp.portfolio_id = :pid AND {_excluded_clause()}
          AND (ST_Intersects(w.geom, pp.geom)
               OR (CAST(:buf AS float) > 0 AND ST_DWithin(w.geom::geography, pp.geom::geography, CAST(:buf AS float))))
        ORDER BY inside DESC, pp.insured_value DESC NULLS LAST, distance_m ASC
        """
    )
    rows = (
        session.execute(sql, {"eid": event_id, "pid": portfolio_id, "buf": buffer_m})
        .mappings()
        .all()
    )
    hits: list[StressHit] = []
    n_inside = n_buffer = n_with_value = n_with_terms = 0
    tiv_inside = repl_inside = gross_total = net_total = 0.0
    any_tiv = any_repl = False
    basis_counts: dict[str, int] = {}
    for r in rows:
        inside = bool(r["inside"])
        exposed, basis, lim, gross, net = hit_loss(
            damage_ratio=damage_ratio,
            insured_value=_f(r["insured_value"]),
            property_value=_f(r["property_value"]),
            contents_value=_f(r["contents_value"]),
            deductible=_f(r["deductible"]),
            limit=_f(r["policy_limit"]),
        )
        if not inside:
            # near-miss: reported, not totalled
            gross = net = None
        hits.append(
            StressHit(
                property_id=r["property_id"],
                policy_id=r["policy_id"],
                row_number=int(r["row_number"]),
                lat=float(r["lat"]),
                lon=float(r["lon"]),
                county_fips=r["county_fips"],
                hit_kind="inside" if inside else "within_buffer",
                distance_m=round(float(r["distance_m"] or 0.0), 1),
                insured_value=_f(r["insured_value"]),
                property_value=_f(r["property_value"]),
                contents_value=_f(r["contents_value"]),
                exposed_value=exposed,
                exposed_value_basis=basis,
                deductible=_f(r["deductible"]),
                limit=lim,
                gross_loss=gross,
                net_insured_loss=net,
                composite_score=None
                if r["composite_score"] is None
                else float(r["composite_score"]),
                duplicate_kind=r["duplicate_kind"],
            )
        )
        if inside:
            n_inside += 1
            basis_counts[basis] = basis_counts.get(basis, 0) + 1
            if r["insured_value"] is not None:
                tiv_inside += float(r["insured_value"])
                any_tiv = True
            if r["property_value"] is not None or r["contents_value"] is not None:
                repl_inside += float(r["property_value"] or 0) + float(r["contents_value"] or 0)
                any_repl = True
            if gross is not None:
                n_with_value += 1
                gross_total += gross
            if net is not None:
                n_with_terms += 1
                net_total += net
        else:
            n_buffer += 1
    tiv_all = totals_all["total_insured_value"]
    totals = {
        "n_exposures_portfolio": totals_all["n_exposures"],
        "n_exposures_inside": n_inside,
        "n_exposures_within_buffer": n_buffer,
        "n_with_value": n_with_value,
        "n_with_terms": n_with_terms,
        "n_without_terms": n_with_value - n_with_terms,
        "total_insured_value_portfolio": tiv_all,
        "total_insured_value_inside": tiv_inside if any_tiv else None,
        "share_of_tiv_pct": round(100.0 * tiv_inside / tiv_all, 2) if any_tiv and tiv_all else None,
        "total_replacement_value_inside": repl_inside if any_repl else None,
        "gross_loss": round(gross_total, 2) if n_with_value else None,
        "net_insured_loss": round(net_total, 2) if n_with_terms else None,
        "exposed_value_basis_counts": basis_counts,
        "sensitivity": [
            {
                "damage_ratio": round(dr, 4),
                "gross_loss": round(gross_total / damage_ratio * dr, 2) if n_with_value else None,
            }
            for dr in sorted({min(damage_ratio * m, 1.0) for m in (0.5, 1.0, 1.5)})
        ],
    }
    caveats = [
        f"Damage ratio {damage_ratio:g} is an assumption applied uniformly to every exposure inside the perimeter.",
        "Net loss is summed only over exposures with policy terms; the gross total covers every exposure with a value.",
    ]
    if n_inside == 0:
        caveats.append(
            "No exposure lies inside this perimeter; the event misses the portfolio as uploaded."
        )
    return StressTestResult(
        portfolio_id=portfolio_id,
        event=event,
        damage_ratio=damage_ratio,
        buffer_m=buffer_m,
        generated_at=datetime.now(UTC).isoformat(),
        totals=totals,
        hits=hits,
        perimeter=perimeter,
        assumptions=list(ASSUMPTIONS),
        caveats=caveats,
    )


def hits_to_rows(result: StressTestResult) -> list[dict[str, Any]]:
    """Flat rows for CSV export."""
    ev = result.event
    out = []
    for h in result.hits:
        d = asdict(h)
        d.update(
            {
                "event_id": ev["event_id"],
                "fire_name": ev.get("fire_name"),
                "fire_year": ev.get("year"),
                "damage_ratio": result.damage_ratio,
            }
        )
        out.append(d)
    return out

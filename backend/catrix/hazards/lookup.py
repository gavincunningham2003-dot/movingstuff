"""PostGIS lookups shared by hazard modules.

Points are sent to the database in batches as a ``VALUES`` list so that a single query
answers hundreds of locations using the GiST indexes; distances use geography
(``ST_DWithin`` on ``geography``) or EPSG:3310 so nothing is measured in degrees.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from catrix.db.models import HazardSource
from catrix.ingest.store import chunked

BATCH = 500


def _values_clause(rows: Iterable[tuple[int, float, float]]) -> str:
    return ", ".join(f"({i}, {lon!r}, {lat!r})" for i, lon, lat in rows)


def source_state(session: Session, source_id: str) -> tuple[str, str | None]:
    row = session.get(HazardSource, source_id)
    if row is None:
        return "never_ingested", None
    return row.ingestion_status, row.version


def polygon_lookup(
    session: Session,
    hazard_id: str,
    points: pd.DataFrame,
    *,
    priority_sql: str = "class_code DESC NULLS LAST",
) -> dict[int, dict[str, Any]]:
    """For each point index return the attributes of the containing polygon (highest
    priority first when several overlap). Points with no containing polygon are absent."""
    out: dict[int, dict[str, Any]] = {}
    rows = list(
        zip(points.index, points["lon"].astype(float), points["lat"].astype(float), strict=True)
    )
    for chunk in chunked(rows, BATCH):
        sql = text(
            f"""
            WITH pts(i, lon, lat) AS (VALUES {_values_clause(chunk)})
            SELECT DISTINCT ON (pts.i) pts.i AS i, h.classification, h.class_code, h.attributes,
                   h.source_object_id
            FROM pts
            JOIN hazard_geometries h
              ON h.hazard_id = :hazard_id
             AND ST_Contains(h.geom, ST_SetSRID(ST_MakePoint(pts.lon, pts.lat), 4326))
            ORDER BY pts.i, {priority_sql}
            """
        )
        for r in session.execute(sql, {"hazard_id": hazard_id}).mappings():
            out[int(r["i"])] = {
                "classification": r["classification"],
                "class_code": r["class_code"],
                "attributes": r["attributes"] or {},
                "source_object_id": r["source_object_id"],
            }
    return out


def polygon_count(session: Session, hazard_id: str) -> int:
    return int(
        session.execute(
            text("SELECT count(*) FROM hazard_geometries WHERE hazard_id = :h"), {"h": hazard_id}
        ).scalar_one()
    )


def county_has_polygons(
    session: Session, hazard_id: str, county_fips: Iterable[str]
) -> dict[str, bool]:
    fips = [f for f in set(county_fips) if f]
    if not fips:
        return {}
    sql = text(
        """
        SELECT r.fips, EXISTS (
            SELECT 1 FROM hazard_geometries h
            WHERE h.hazard_id = :h AND h.geom && r.geom AND ST_Intersects(h.geom, r.geom)
        ) AS has_any
        FROM regions r WHERE r.fips = ANY(:fips)
        """
    )
    return {
        r["fips"]: bool(r["has_any"])
        for r in session.execute(sql, {"h": hazard_id, "fips": fips}).mappings()
    }


def fire_history_lookup(
    session: Session,
    points: pd.DataFrame,
    *,
    since_year: int,
    radius_m: float,
    until_year: int | None = None,
) -> dict[int, dict[str, Any]]:
    """Fire-perimeter neighbourhood statistics for each point (metres via geography).

    ``until_year`` (inclusive) lets the ML feature builder restrict to years ≤ T so that
    no target-year fire leaks into the predictors.
    """
    out: dict[int, dict[str, Any]] = {}
    rows = list(
        zip(points.index, points["lon"].astype(float), points["lat"].astype(float), strict=True)
    )
    until = until_year if until_year is not None else 9999
    for chunk in chunked(rows, BATCH):
        sql = text(
            f"""
            WITH pts(i, lon, lat) AS (VALUES {_values_clause(chunk)}),
            p AS (SELECT i, ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography AS g FROM pts)
            SELECT p.i AS i,
                   count(w.id) FILTER (WHERE w.year >= :since) AS n_fires_radius,
                   max(w.year) AS last_fire_year,
                   bool_or(ST_Intersects(w.geom, p.g::geometry)) AS inside_any,
                   max(w.year) FILTER (WHERE ST_Intersects(w.geom, p.g::geometry)) AS last_inside_year,
                   sum(w.gis_acres) FILTER (WHERE w.year >= :since) AS acres_radius
            FROM p
            LEFT JOIN wildfire_events w
              ON w.year <= :until AND ST_DWithin(w.geom::geography, p.g, :radius)
            GROUP BY p.i
            """
        )
        for r in session.execute(
            sql, {"since": since_year, "until": until, "radius": radius_m}
        ).mappings():
            out[int(r["i"])] = dict(r)
    return out


def nearest_line_distance(
    session: Session, hazard_id: str, points: pd.DataFrame, max_m: float = 100_000
) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    rows = list(
        zip(points.index, points["lon"].astype(float), points["lat"].astype(float), strict=True)
    )
    for chunk in chunked(rows, BATCH):
        sql = text(
            f"""
            WITH pts(i, lon, lat) AS (VALUES {_values_clause(chunk)})
            SELECT pts.i AS i, l.name, ST_Distance(l.geom::geography, ST_SetSRID(ST_MakePoint(pts.lon, pts.lat), 4326)::geography) AS dist_m
            FROM pts
            CROSS JOIN LATERAL (
                SELECT name, geom FROM hazard_lines
                WHERE hazard_id = :h
                ORDER BY geom <-> ST_SetSRID(ST_MakePoint(pts.lon, pts.lat), 4326)
                LIMIT 1
            ) l
            """
        )
        for r in session.execute(sql, {"h": hazard_id}).mappings():
            if r["dist_m"] is not None and r["dist_m"] <= max_m:
                out[int(r["i"])] = {"name": r["name"], "distance_m": float(r["dist_m"])}
    return out


def earthquake_history_lookup(
    session: Session, points: pd.DataFrame, radius_m: float = 50_000
) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    rows = list(
        zip(points.index, points["lon"].astype(float), points["lat"].astype(float), strict=True)
    )
    for chunk in chunked(rows, BATCH):
        sql = text(
            f"""
            WITH pts(i, lon, lat) AS (VALUES {_values_clause(chunk)}),
            p AS (SELECT i, ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography AS g FROM pts)
            SELECT p.i AS i,
                   count(e.id) AS n_m4_50km,
                   count(e.id) FILTER (WHERE e.magnitude >= 5.5) AS n_m55_50km,
                   max(e.magnitude) AS max_mag_50km,
                   min(ST_Distance(e.geom::geography, p.g)) FILTER (WHERE e.magnitude >= 5.5) AS nearest_m55_m
            FROM p LEFT JOIN earthquake_events e ON ST_DWithin(e.geom::geography, p.g, :radius)
            GROUP BY p.i
            """
        )
        for r in session.execute(sql, {"radius": radius_m}).mappings():
            out[int(r["i"])] = dict(r)
    return out


def nearest_structure(
    session: Session, points: pd.DataFrame, max_m: float = 30.0
) -> dict[int, dict[str, Any]]:
    """Nearest NSI structure (within ``max_m``) for each point, with its attributes."""
    out: dict[int, dict[str, Any]] = {}
    rows = list(
        zip(points.index, points["lon"].astype(float), points["lat"].astype(float), strict=True)
    )
    for chunk in chunked(rows, BATCH):
        sql = text(
            f"""
            WITH pts(i, lon, lat) AS (VALUES {_values_clause(chunk)})
            SELECT pts.i AS i, s.id AS structure_id, s.bldgtype, s.occtype, s.damage_category,
                   s.num_story, s.sqft, s.found_type, s.found_ht_ft, s.ground_elev_ft, s.year_built,
                   s.val_struct_usd, s.firmzone, s.static_bfe_ft,
                   ST_Distance(s.geom::geography, ST_SetSRID(ST_MakePoint(pts.lon, pts.lat), 4326)::geography) AS dist_m
            FROM pts
            CROSS JOIN LATERAL (
                SELECT * FROM structures
                ORDER BY geom <-> ST_SetSRID(ST_MakePoint(pts.lon, pts.lat), 4326)
                LIMIT 1
            ) s
            """
        )
        for r in session.execute(sql).mappings():
            if r["dist_m"] is not None and r["dist_m"] <= max_m:
                out[int(r["i"])] = dict(r)
    return out


def regional_lookup(
    session: Session, metrics: list[str], county_fips: Iterable[str]
) -> dict[str, dict[str, Any]]:
    fips = [f for f in set(county_fips) if f]
    if not fips:
        return {}
    sql = text(
        "SELECT county_fips, metric, value, rating, version FROM regional_indices WHERE county_fips = ANY(:fips) AND metric = ANY(:metrics)"
    )
    out: dict[str, dict[str, Any]] = {}
    for r in session.execute(sql, {"fips": fips, "metrics": metrics}).mappings():
        out.setdefault(r["county_fips"], {})[r["metric"]] = {
            "value": r["value"],
            "rating": r["rating"],
            "version": r["version"],
        }
    return out

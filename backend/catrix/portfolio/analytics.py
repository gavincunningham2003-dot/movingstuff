"""Portfolio aggregation: hazard distribution, county/regional summaries, concentration
cells, top exposures and financial scenario totals.

Rules enforced here:
* hazard indicators are counted/averaged per hazard — never summed across hazards as money;
* only user-supplied monetary values are summed, and each kind is summed separately:
  sum insured (TIV), building replacement value, contents value, policy limits;
* **exact** duplicates (same id at the same location) are excluded from counts and totals;
  **shared-location** exposures (different ids at one site) are kept — that *is* the
  accumulation an underwriter wants to see — and surfaced in ``location_accumulation``;
* scenario totals report gross and net separately, with the number of exposures that
  had policy terms, plus damage-ratio sensitivity totals.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from catrix.geo.spatial import grid_cell_centroid, grid_cell_ids
from catrix.ingest.store import rows

TOP_N = 25
TOP_LOCATIONS = 15
CONCENTRATION_CELL_KM = 10.0
EXCLUDED_DUPLICATE_KINDS = ("exact",)


def _share(n: int, total: int) -> float:
    return round(100.0 * n / total, 1) if total else 0.0


def _sum_or_none(series: pd.Series) -> float | None:
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float(s.sum()) if len(s) else None


def _money(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype(float).fillna(0.0)


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise optional columns so older callers (and stored rows) keep working."""
    df = df.copy()
    for col in ("contents_value", "policy_limit", "deductible", "policy_id", "location_key"):
        if col not in df.columns:
            df[col] = None
    if "duplicate_kind" not in df.columns:
        # legacy: every flagged row was a location duplicate under a different id
        df["duplicate_kind"] = [
            "shared_location" if pd.notna(v) else None for v in df["is_duplicate_of"]
        ]
    if df["location_key"].isna().all() and len(df):
        df["location_key"] = [
            f"{round(float(la), 4)}:{round(float(lo), 4)}"
            for la, lo in zip(df["lat"], df["lon"], strict=True)
        ]
    return df


def summarise_portfolio(df: pd.DataFrame, county_names: dict[str, str]) -> dict[str, Any]:
    """``df`` columns: property_id, policy_id, lat, lon, county_fips, insured_value,
    property_value, contents_value, policy_limit, deductible, is_duplicate_of,
    duplicate_kind, location_key, composite_score, hazards (dict hazard_id → result dict),
    financial (dict|None).
    """
    df = _prepare(df)
    excluded = df["duplicate_kind"].isin(EXCLUDED_DUPLICATE_KINDS)
    work = df[~excluded].copy()
    n = len(work)
    warnings: list[str] = []
    n_dup = int(excluded.sum())
    n_shared = int((work["duplicate_kind"] == "shared_location").sum())
    n_repeated = int((work["duplicate_kind"] == "repeated_id").sum())
    n_locations = int(work["location_key"].nunique()) if n else 0
    if n_dup:
        warnings.append(
            f"{n_dup} exact duplicate rows (same property_id at the same location) excluded from counts and totals."
        )
    if n_shared:
        warnings.append(
            f"{n_shared} exposures share a location with another exposure; they are kept and "
            f"shown under location accumulation ({n} exposures at {n_locations} distinct locations)."
        )
    if n_repeated:
        warnings.append(
            f"{n_repeated} exposures reuse a property_id seen at another location (kept; check the source file)."
        )

    # Hazard distribution and coverage ---------------------------------------------------
    hazard_dist: dict[str, Counter] = defaultdict(Counter)
    coverage: dict[str, Counter] = defaultdict(Counter)
    for hz in work["hazards"]:
        for hid, r in (hz or {}).items():
            coverage[hid][r.get("coverage_status", "unavailable")] += 1
            if r.get("coverage_status") == "covered":
                hazard_dist[hid][str(r.get("classification") or "covered")] += 1
            elif r.get("coverage_status") == "not_in_hazard_area":
                hazard_dist[hid]["not in hazard area"] += 1
            else:
                hazard_dist[hid][r.get("coverage_status", "unavailable")] += 1

    # Financial -------------------------------------------------------------------------------
    tiv = work["insured_value"].dropna()
    pv = work["property_value"].dropna()
    cv = work["contents_value"].dropna()
    lim = work["policy_limit"].dropna()
    ded = work["deductible"].dropna()
    financial: dict[str, Any] | None = None
    if len(tiv) or len(pv) or len(cv):
        scenario_totals: dict[str, dict[str, Any]] = {}
        basis_counts: Counter[str] = Counter()
        limit_basis_counts: Counter[str] = Counter()
        for fin in work["financial"]:
            fin = fin or {}
            if fin.get("exposed_value_basis"):
                basis_counts[str(fin["exposed_value_basis"])] += 1
            if fin.get("limit_basis"):
                limit_basis_counts[str(fin["limit_basis"])] += 1
            for s in fin.get("scenarios", []):
                t = scenario_totals.setdefault(
                    s["scenario_id"],
                    {
                        "scenario_id": s["scenario_id"],
                        "hazard_id": s["hazard_id"],
                        "description": s["description"],
                        "damage_ratio": s["damage_ratio"],
                        "n_properties": 0,
                        "n_with_value": 0,
                        "exposed_value": 0.0,
                        "gross_loss": 0.0,
                        "net_insured_loss": 0.0,
                        "n_with_terms": 0,
                        "n_without_terms": 0,
                        "sensitivity": {},
                    },
                )
                t["n_properties"] += 1
                if s.get("gross_loss") is not None:
                    t["n_with_value"] += 1
                t["exposed_value"] += s.get("exposed_value") or 0.0
                t["gross_loss"] += s.get("gross_loss") or 0.0
                if s.get("net_insured_loss") is not None:
                    t["net_insured_loss"] += s["net_insured_loss"]
                    t["n_with_terms"] += 1
                elif s.get("gross_loss") is not None:
                    t["n_without_terms"] += 1
                for row in s.get("sensitivity", []):
                    m = str(row["multiplier"])
                    acc = t["sensitivity"].setdefault(
                        m,
                        {
                            "multiplier": row["multiplier"],
                            "gross_loss": 0.0,
                            "net_insured_loss": 0.0,
                            "n_with_terms": 0,
                        },
                    )
                    acc["gross_loss"] += row.get("gross_loss") or 0.0
                    if row.get("net_insured_loss") is not None:
                        acc["net_insured_loss"] += row["net_insured_loss"]
                        acc["n_with_terms"] += 1
        for t in scenario_totals.values():
            if t["n_with_terms"] == 0:
                t["net_insured_loss"] = None
            sens = sorted(t["sensitivity"].values(), key=lambda r: r["multiplier"])
            for r in sens:
                if r["n_with_terms"] == 0:
                    r["net_insured_loss"] = None
                r["gross_loss"] = round(r["gross_loss"], 2)
                if r["net_insured_loss"] is not None:
                    r["net_insured_loss"] = round(r["net_insured_loss"], 2)
            t["sensitivity"] = sens
            t["exposed_value"] = round(t["exposed_value"], 2)
            t["gross_loss"] = round(t["gross_loss"], 2)
            if t["net_insured_loss"] is not None:
                t["net_insured_loss"] = round(t["net_insured_loss"], 2)
        total_replacement = (
            None
            if not (len(pv) or len(cv))
            else float(_money(work["property_value"]).sum() + _money(work["contents_value"]).sum())
        )
        financial = {
            "n_exposures": n,
            "n_with_insured_value": int(len(tiv)),
            "n_with_property_value": int(len(pv)),
            "n_with_contents_value": int(len(cv)),
            "n_with_limit": int(len(lim)),
            "n_with_deductible": int(len(ded)),
            "total_insured_value": _sum_or_none(tiv),
            "total_property_value": _sum_or_none(pv),
            "total_contents_value": _sum_or_none(cv),
            "total_replacement_value": total_replacement,
            "total_policy_limit": _sum_or_none(lim),
            "total_deductible": _sum_or_none(ded),
            "exposed_value_basis_counts": dict(basis_counts),
            "limit_basis_counts": dict(limit_basis_counts),
            "scenarios": sorted(scenario_totals.values(), key=lambda t: -(t["gross_loss"] or 0)),
            "note": (
                "Scenario totals are simple sums of per-exposure scenario losses for the same "
                "assumed event type; they are not correlated-event or expected annual losses. "
                "Gross loss is physical damage to the replacement value; net insured loss "
                "applies each exposure's deductible and limit (the sum insured when no limit "
                "is given) and is only summed over exposures that have terms."
            ),
        }
        if len(tiv) < n:
            warnings.append(
                f"{n - len(tiv)} exposures have no sum insured; they are excluded from the total insured value."
            )
        if basis_counts.get("sum_insured_proxy"):
            warnings.append(
                f"{basis_counts['sum_insured_proxy']} exposures have no replacement value; "
                "their sum insured is used as a proxy for the physical value at risk."
            )
    else:
        warnings.append("No monetary values supplied; financial scenarios not computed.")

    # Location accumulation ------------------------------------------------------------
    total_tiv_all = _sum_or_none(tiv)
    location_accumulation: list[dict[str, Any]] = []
    if n:
        for key, g in work.groupby("location_key"):
            if len(g) < 2:
                continue
            loc_tiv = _sum_or_none(g["insured_value"])
            repl = (
                None
                if not (g["property_value"].notna().any() or g["contents_value"].notna().any())
                else float(_money(g["property_value"]).sum() + _money(g["contents_value"]).sum())
            )
            scores_g = g["composite_score"].dropna()
            location_accumulation.append(
                {
                    "location_key": str(key),
                    "lat": round(float(g["lat"].iloc[0]), 6),
                    "lon": round(float(g["lon"].iloc[0]), 6),
                    "n_exposures": int(len(g)),
                    "property_ids": [str(x) for x in g["property_id"].tolist()[:10]],
                    "policy_ids": sorted(
                        {str(x) for x in g["policy_id"].tolist() if isinstance(x, str) and x}
                    )[:10],
                    "total_insured_value": loc_tiv,
                    "total_replacement_value": repl,
                    "share_of_tiv_pct": (
                        round(100.0 * loc_tiv / total_tiv_all, 2)
                        if loc_tiv is not None and total_tiv_all
                        else None
                    ),
                    "composite_score": float(scores_g.max()) if len(scores_g) else None,
                }
            )
        location_accumulation.sort(
            key=lambda c: (-(c["total_insured_value"] or 0), -c["n_exposures"])
        )
        location_accumulation = location_accumulation[:TOP_LOCATIONS]

    # County summaries --------------------------------------------------------------------
    by_county: list[dict[str, Any]] = []
    for fips, g in work.groupby(work["county_fips"].fillna("unknown")):
        fh = [h.get("wildfire_fhsz", {}) for h in g["hazards"]]
        fl = [h.get("flood_nfhl", {}) for h in g["hazards"]]
        pga = [h.get("earthquake_pga", {}).get("value") for h in g["hazards"]]
        pga_vals = [v for v in pga if v is not None]
        by_county.append(
            {
                "county": county_names.get(fips, None if fips == "unknown" else fips),
                "county_fips": None if fips == "unknown" else fips,
                "n_properties": int(len(g)),
                "share_pct": _share(len(g), n),
                "total_insured_value": float(g["insured_value"].sum())
                if g["insured_value"].notna().any()
                else None,
                "mean_composite_score": float(g["composite_score"].mean())
                if g["composite_score"].notna().any()
                else None,
                "n_fhsz_very_high": int(
                    sum(1 for r in fh if r.get("classification") == "Very High")
                ),
                "n_sfha": int(sum(1 for r in fl if (r.get("details") or {}).get("sfha") is True)),
                "mean_pga_2pc50_g": float(np.mean(pga_vals)) if pga_vals else None,
            }
        )
    by_county.sort(key=lambda c: -c["n_properties"])

    # Concentration cells -----------------------------------------------------------------
    cells = grid_cell_ids(
        work["lon"].to_numpy(float), work["lat"].to_numpy(float), CONCENTRATION_CELL_KM
    )
    work = work.assign(_cell=cells)
    total_tiv = float(tiv.sum()) if len(tiv) else None
    concentration: list[dict[str, Any]] = []
    for cell, g in work.groupby("_cell"):
        lon, lat = grid_cell_centroid(str(cell), CONCENTRATION_CELL_KM)
        cell_tiv = float(g["insured_value"].sum()) if g["insured_value"].notna().any() else None
        dom: Counter[str] = Counter()
        for h in g["hazards"]:
            for hid in ("wildfire_fhsz", "flood_nfhl", "earthquake_pga", "tsunami", "landslide"):
                r = h.get(hid, {})
                if r.get("coverage_status") == "covered" and (
                    r.get("classification") in ("Very High", "High")
                    or (hid == "flood_nfhl" and r.get("value") == 3.0)
                    or (hid == "earthquake_pga" and (r.get("value") or 0) >= 0.6)
                ):
                    dom[hid] += 1
        concentration.append(
            {
                "cell_id": str(cell),
                "centroid_lat": round(lat, 5),
                "centroid_lon": round(lon, 5),
                "n_properties": int(len(g)),
                "total_insured_value": cell_tiv,
                "share_of_tiv_pct": (
                    round(100.0 * cell_tiv / total_tiv, 1)
                    if cell_tiv is not None and total_tiv
                    else None
                ),
                "dominant_hazard": dom.most_common(1)[0][0] if dom else None,
            }
        )
    concentration.sort(key=lambda c: (-(c["total_insured_value"] or 0), -c["n_properties"]))
    concentration = concentration[:50]

    # Top exposures ----------------------------------------------------------------------
    def drivers(h: dict[str, Any]) -> list[str]:
        d = []
        fh = h.get("wildfire_fhsz", {})
        if fh.get("classification") in ("Very High", "High"):
            d.append(f"FHSZ {fh['classification']}")
        fl = h.get("flood_nfhl", {})
        if (fl.get("details") or {}).get("sfha"):
            d.append("SFHA flood zone")
        eq = h.get("earthquake_pga", {})
        if (eq.get("value") or 0) >= 0.5:
            d.append(f"PGA {eq['value']:.2f} g (2%/50y)")
        if h.get("tsunami", {}).get("coverage_status") == "covered":
            d.append("Tsunami hazard area")
        ls = h.get("landslide", {})
        if (ls.get("value") or 0) >= 8:
            d.append(f"Landslide class {int(ls['value'])}")
        if h.get("liquefaction", {}).get("coverage_status") == "covered":
            d.append("Liquefaction zone")
        return d

    ranked = work.sort_values(
        ["composite_score", "insured_value"], ascending=[False, False], na_position="last"
    ).head(TOP_N)
    top = [
        {
            "property_id": r.property_id,
            "lat": float(r.lat),
            "lon": float(r.lon),
            "county": county_names.get(r.county_fips) if isinstance(r.county_fips, str) else None,
            "insured_value": None if pd.isna(r.insured_value) else float(r.insured_value),
            "composite_score": None if pd.isna(r.composite_score) else float(r.composite_score),
            "drivers": drivers(r.hazards or {}),
        }
        for r in rows(ranked)
    ]

    scores = work["composite_score"].dropna()
    summary = {
        "n_properties": n,
        "n_exposures": n,
        "n_locations": n_locations,
        "n_duplicates_excluded": n_dup,
        "n_shared_location_exposures": n_shared,
        "n_repeated_ids": n_repeated,
        "n_counties": int(work["county_fips"].nunique()),
        "n_fhsz_very_high": int(
            sum(
                1
                for h in work["hazards"]
                if h.get("wildfire_fhsz", {}).get("classification") == "Very High"
            )
        ),
        "n_fhsz_high": int(
            sum(
                1
                for h in work["hazards"]
                if h.get("wildfire_fhsz", {}).get("classification") == "High"
            )
        ),
        "n_sfha": int(
            sum(
                1
                for h in work["hazards"]
                if (h.get("flood_nfhl", {}).get("details") or {}).get("sfha") is True
            )
        ),
        "n_pga_ge_0_5g": int(
            sum(
                1 for h in work["hazards"] if (h.get("earthquake_pga", {}).get("value") or 0) >= 0.5
            )
        ),
        "n_tsunami_area": int(
            sum(
                1
                for h in work["hazards"]
                if h.get("tsunami", {}).get("coverage_status") == "covered"
            )
        ),
        "composite_score_mean": float(scores.mean()) if len(scores) else None,
        "composite_score_p90": float(scores.quantile(0.9)) if len(scores) else None,
        "n_scored": int(len(scores)),
        "max_cell_share_of_tiv_pct": concentration[0]["share_of_tiv_pct"]
        if concentration
        else None,
    }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": summary,
        "hazard_distribution": {k: dict(v) for k, v in hazard_dist.items()},
        "coverage": {k: dict(v) for k, v in coverage.items()},
        "by_county": by_county,
        "concentration": concentration,
        "top_exposures": top,
        "location_accumulation": location_accumulation,
        "financial": financial,
        "warnings": warnings,
    }

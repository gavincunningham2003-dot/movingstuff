"""Portfolio aggregation: hazard distribution, county/regional summaries, concentration
cells, top exposures and financial scenario totals.

Rules enforced here:
* hazard indicators are counted/averaged per hazard — never summed across hazards as money;
* only user-supplied monetary values are summed;
* duplicates (same location) are excluded from counts/TIV totals and reported separately.
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
CONCENTRATION_CELL_KM = 10.0


def _share(n: int, total: int) -> float:
    return round(100.0 * n / total, 1) if total else 0.0


def summarise_portfolio(df: pd.DataFrame, county_names: dict[str, str]) -> dict[str, Any]:
    """``df`` columns: property_id, lat, lon, county_fips, insured_value, property_value,
    is_duplicate_of, composite_score, hazards (dict hazard_id → result dict), financial (dict|None).
    """
    work = df[df["is_duplicate_of"].isna()].copy()
    n = len(work)
    warnings: list[str] = []
    n_dup = int(df["is_duplicate_of"].notna().sum())
    if n_dup:
        warnings.append(f"{n_dup} duplicate locations excluded from totals.")

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
    financial: dict[str, Any] | None = None
    if len(tiv) or len(pv):
        scenario_totals: dict[str, dict[str, Any]] = {}
        for fin in work["financial"]:
            for s in (fin or {}).get("scenarios", []):
                t = scenario_totals.setdefault(
                    s["scenario_id"],
                    {
                        "scenario_id": s["scenario_id"],
                        "hazard_id": s["hazard_id"],
                        "description": s["description"],
                        "damage_ratio": s["damage_ratio"],
                        "n_properties": 0,
                        "exposed_value": 0.0,
                        "gross_loss": 0.0,
                        "net_insured_loss": 0.0,
                        "n_with_terms": 0,
                    },
                )
                t["n_properties"] += 1
                t["exposed_value"] += s.get("exposed_value") or 0.0
                t["gross_loss"] += s.get("gross_loss") or 0.0
                if s.get("net_insured_loss") is not None:
                    t["net_insured_loss"] += s["net_insured_loss"]
                    t["n_with_terms"] += 1
        for t in scenario_totals.values():
            if t["n_with_terms"] == 0:
                t["net_insured_loss"] = None
        financial = {
            "n_with_insured_value": int(len(tiv)),
            "n_with_property_value": int(len(pv)),
            "total_insured_value": float(tiv.sum()) if len(tiv) else None,
            "total_property_value": float(pv.sum()) if len(pv) else None,
            "scenarios": sorted(scenario_totals.values(), key=lambda t: -(t["gross_loss"] or 0)),
            "note": "Scenario totals are simple sums of per-property scenario losses for the same assumed event type; they are not correlated-event or expected annual losses.",
        }
        if len(tiv) < n:
            warnings.append(
                f"{n - len(tiv)} properties have no insured value; they are excluded from monetary totals."
            )
    else:
        warnings.append("No monetary values supplied; financial scenarios not computed.")

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
        "n_duplicates_excluded": n_dup,
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
        "financial": financial,
        "warnings": warnings,
    }

"""Flood hazard: FEMA NFHL flood zones (regulatory floodplain classification).

Regulatory zones are not annual loss probabilities. ``A*``/``V*`` zones are Special Flood
Hazard Areas (1% annual-chance floodplain); shaded X is the 0.2% annual-chance area;
unshaded X is outside both; ``D`` is undetermined. Where no NFHL polygon exists for a county
in the ingested extract, locations are reported as ``outside_coverage``.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from catrix.hazards.base import HazardInfo, HazardModule, HazardResult
from catrix.hazards.lookup import county_has_polygons, polygon_count, polygon_lookup, source_state

SFHA_PREFIXES = ("A", "V")


def classify_zone(zone: str | None, subtype: str | None, sfha_tf: str | None) -> tuple[str, float]:
    """Return (label, ordinal) where ordinal 3 = SFHA, 2 = 0.2% annual chance, 1 = minimal, 0 = undetermined."""
    z = (zone or "").upper().strip()
    st = (subtype or "").upper()
    if sfha_tf == "T" or z.startswith(SFHA_PREFIXES):
        return ("Special Flood Hazard Area (1% annual chance)", 3.0)
    if z == "X" and ("0.2" in st or "LEVEE" in st or "SHADED" in st):
        return ("0.2% annual chance (moderate)", 2.0)
    if z == "X":
        return ("Minimal flood hazard (outside 0.2% floodplain)", 1.0)
    if z == "D":
        return ("Undetermined (zone D)", 0.0)
    if z in ("OPEN WATER", "AREA NOT INCLUDED"):
        return (z.title(), 0.0)
    return (z or "Unknown", 0.0)


class FloodNFHLModule(HazardModule):
    info = HazardInfo(
        hazard_id="flood_nfhl",
        name="Flood – FEMA NFHL zone",
        description="FEMA National Flood Hazard Layer flood zone and Special Flood Hazard Area flag.",
        source_ids=("fema_nfhl",),
        method="static_overlay",
        units="regulatory zone ordinal (0-3)",
        probability_horizon=None,
        scale_note="Regulatory floodplain mapping (1% / 0.2% annual-chance boundaries). It is a "
        "mapped boundary, not a property-level annual loss probability, and it excludes "
        "pluvial (surface-water) flooding.",
        map_layer_available=True,
    )

    def assess(self, session: Session, points: pd.DataFrame) -> list[HazardResult]:
        status, version = source_state(session, self.info.source_ids[0])
        if status != "ingested" or polygon_count(session, "flood_nfhl") == 0:
            return self.unavailable(
                len(points), f"NFHL not ingested (status={status}); see data_sources.yaml notes"
            )
        hits = polygon_lookup(
            session, "flood_nfhl", points, priority_sql="(attributes->>'SFHA_TF') DESC NULLS LAST"
        )
        coverage = (
            county_has_polygons(
                session,
                "flood_nfhl",
                points.get("county_fips", pd.Series(dtype=str)).dropna().unique(),
            )
            if "county_fips" in points
            else {}
        )
        out = []
        for i, r in zip(points.index, points.itertuples(), strict=True):
            h = hits.get(int(i))
            if h is None:
                fips = getattr(r, "county_fips", None)
                mapped = coverage.get(fips, False) if fips else False
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        self.info.source_ids[0],
                        "not_in_hazard_area" if mapped else "outside_coverage",
                        source_version=version,
                        method="static_overlay",
                        confidence="medium" if mapped else "low",
                        caveats=[
                            "No NFHL polygon at this location"
                            + (
                                "; county has NFHL coverage so the location is likely in an unmapped/area-not-included region."
                                if mapped
                                else "; no NFHL polygons were ingested for this county."
                            )
                        ],
                    )
                )
                continue
            a = h["attributes"] or {}
            label, ordinal = classify_zone(a.get("FLD_ZONE"), a.get("ZONE_SUBTY"), a.get("SFHA_TF"))
            out.append(
                HazardResult(
                    self.info.hazard_id,
                    self.info.name,
                    self.info.source_ids[0],
                    "covered",
                    source_version=version,
                    classification=label,
                    value=ordinal,
                    units=self.info.units,
                    method="static_overlay",
                    confidence="high",
                    details={
                        "fld_zone": a.get("FLD_ZONE"),
                        "zone_subtype": a.get("ZONE_SUBTY"),
                        "sfha": a.get("SFHA_TF") == "T",
                        "static_bfe_ft": a.get("STATIC_BFE")
                        if a.get("STATIC_BFE") not in (None, -9999)
                        else None,
                        "dfirm_id": a.get("DFIRM_ID"),
                    },
                    caveats=[self.info.scale_note],
                )
            )
        return out

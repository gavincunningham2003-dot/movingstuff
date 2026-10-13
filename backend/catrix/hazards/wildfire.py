"""Wildfire hazard modules: FHSZ overlay and historical perimeter exposure."""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from catrix.hazards.base import HazardInfo, HazardModule, HazardResult
from catrix.hazards.lookup import fire_history_lookup, polygon_count, polygon_lookup, source_state

FHSZ_ORDER = ["Very High", "High", "Moderate"]
FHSZ_CODE = {3: "Very High", 2: "High", 1: "Moderate"}


class WildfireFHSZModule(HazardModule):
    info = HazardInfo(
        hazard_id="wildfire_fhsz",
        name="Wildfire – Fire Hazard Severity Zone",
        description="CAL FIRE regulatory Fire Hazard Severity Zone (Moderate / High / Very High) "
        "for State and Local Responsibility Areas.",
        source_ids=("calfire_fhsz_2024",),
        method="static_overlay",
        units=None,
        probability_horizon=None,
        scale_note="Ordinal regulatory classification based on fuel, slope, fire weather and "
        "ember production. It is not an annual probability. Areas outside any zone are "
        "unzoned (often urban or non-wildland), not necessarily hazard-free.",
        map_layer_available=True,
    )

    def assess(self, session: Session, points: pd.DataFrame) -> list[HazardResult]:
        status, version = source_state(session, self.info.source_ids[0])
        if status != "ingested" or polygon_count(session, "wildfire_fhsz") == 0:
            return self.unavailable(len(points), f"FHSZ layer not ingested (status={status})")
        hits = polygon_lookup(
            session, "wildfire_fhsz", points, priority_sql="class_code DESC NULLS LAST"
        )
        out = []
        for i in points.index:
            h = hits.get(int(i))
            if h is None:
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        self.info.source_ids[0],
                        "not_in_hazard_area",
                        source_version=version,
                        method="static_overlay",
                        confidence="high",
                        details={"zone": None},
                        caveats=[
                            "Location is outside all mapped FHSZ polygons (unzoned). Unzoned does not mean no wildfire hazard."
                        ],
                    )
                )
            else:
                cls = h["classification"] or FHSZ_CODE.get(h["class_code"] or 0)
                attrs = h["attributes"] or {}
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        self.info.source_ids[0],
                        "covered",
                        source_version=version,
                        classification=cls,
                        value=float(h["class_code"]) if h["class_code"] is not None else None,
                        units="class (1=Moderate,2=High,3=Very High)",
                        method="static_overlay",
                        confidence="high",
                        details={
                            "responsibility_area": attrs.get("SRA22_2"),
                            "seven_class": attrs.get("FHSZ_7Class"),
                        },
                    )
                )
        return out


class WildfirePerimeterHistoryModule(HazardModule):
    info = HazardInfo(
        hazard_id="wildfire_history",
        name="Wildfire – historical perimeter exposure",
        description="Mapped CAL FIRE fire perimeters since 1990: whether the location has ever "
        "been inside a mapped perimeter, and perimeter density within 5 km.",
        source_ids=("calfire_fire_perimeters",),
        method="static_overlay",
        units="perimeters within 5 km since 1990",
        probability_horizon=None,
        scale_note="Counts of mapped historical perimeters. Perimeter mapping is incomplete for "
        "small fires and earlier decades; absence of a perimeter is not evidence of no fire.",
        map_layer_available=True,
    )

    def assess(self, session: Session, points: pd.DataFrame) -> list[HazardResult]:
        status, version = source_state(session, self.info.source_ids[0])
        if status != "ingested":
            return self.unavailable(len(points), f"fire perimeters not ingested (status={status})")
        stats = fire_history_lookup(session, points, since_year=1990, radius_m=5000)
        out = []
        for i in points.index:
            s = stats.get(int(i), {})
            n = int(s.get("n_fires_radius") or 0)
            inside = bool(s.get("inside_any"))
            out.append(
                HazardResult(
                    self.info.hazard_id,
                    self.info.name,
                    self.info.source_ids[0],
                    "covered" if (n > 0 or inside) else "not_in_hazard_area",
                    source_version=version,
                    classification="inside mapped perimeter"
                    if inside
                    else ("perimeters nearby" if n else "no mapped perimeters within 5 km"),
                    value=float(n),
                    units=self.info.units,
                    method="static_overlay",
                    confidence="medium",
                    details={
                        "inside_any_perimeter": inside,
                        "last_perimeter_year_inside": s.get("last_inside_year"),
                        "last_perimeter_year_within_5km": s.get("last_fire_year"),
                        "acres_burned_within_5km_since_1990": float(s["acres_radius"])
                        if s.get("acres_radius") is not None
                        else None,
                    },
                    caveats=[
                        "Historical perimeters measure past mapped fire extent, not building damage."
                    ],
                )
            )
        return out

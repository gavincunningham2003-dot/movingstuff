"""Earthquake hazard modules.

* :class:`EarthquakePGAModule` — probabilistic ground motion (PGA, 2% in 50 years) from the
  CGS MS48 ImageServer (NSHM 2023), with the USGS ASCE 7-16 design-maps service
  (``pgauh``, NSHM 2014-based) as a fallback. Both are explicit exceedance-probability
  values, so this is the one hazard reported on a true probability horizon.
* :class:`FaultProximityModule` — distance to the nearest Quaternary fault (context only).
* :class:`EarthquakeHistoryModule` — M4+/M5.5+ events within 50 km since 1980.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from catrix.hazards.base import HazardInfo, HazardModule, HazardResult
from catrix.hazards.lookup import earthquake_history_lookup, nearest_line_distance, source_state
from catrix.hazards.point_cache import PointCache
from catrix.ingest.arcgis import imageserver_sample
from catrix.ingest.http import SourceUnavailableError, get_json
from catrix.ingest.registry import load_registry
from catrix.logging import get_logger

log = get_logger(__name__)

# Screening bins for PGA (2%/50y). These are CATRIX display bins loosely aligned with
# USGS shaking-intensity thresholds; they are an assumption, not an official classification.
PGA_BINS = [(0.0, 0.2, "Low"), (0.2, 0.4, "Moderate"), (0.4, 0.6, "High"), (0.6, 99.0, "Very High")]


def pga_class(pga_g: float) -> str:
    for lo, hi, label in PGA_BINS:
        if lo <= pga_g < hi:
            return label
    return "Very High"


class EarthquakePGAModule(HazardModule):
    info = HazardInfo(
        hazard_id="earthquake_pga",
        name="Earthquake – peak ground acceleration",
        description="Probabilistic peak ground acceleration with 2% probability of exceedance in "
        "50 years (2,475-year return period), from the USGS National Seismic Hazard Model.",
        source_ids=("cgs_ms48_pga_2pc50", "usgs_designmaps_asce7_16"),
        method="point_service",
        units="g",
        probability_horizon="2% in 50 years",
        scale_note="Ground-motion hazard only: it describes shaking intensity at an explicit "
        "exceedance probability and says nothing about building vulnerability.",
    )

    def __init__(self, *, allow_network: bool = True):
        self.allow_network = allow_network

    def _cgs(self, cache: PointCache, lon: float, lat: float) -> float | None:
        cached = cache.get(lon, lat)
        if cached is not None:
            return cached.get("pga")
        spec = load_registry().get("cgs_ms48_pga_2pc50")
        assert spec.api_url
        val = imageserver_sample("cgs_ms48_pga_2pc50", spec.api_url, lon, lat)
        cache.put(lon, lat, {"pga": val})
        return val

    def _usgs(self, cache: PointCache, lon: float, lat: float) -> float | None:
        cached = cache.get(lon, lat)
        if cached is not None:
            return cached.get("pgauh")
        spec = load_registry().get("usgs_designmaps_asce7_16")
        assert spec.api_url
        data = get_json("usgs_designmaps_asce7_16", spec.api_url.format(lat=lat, lon=lon))
        val = (data.get("response") or {}).get("data", {}).get("pgauh")
        cache.put(lon, lat, {"pgauh": val})
        return val

    def assess(self, session: Session, points: pd.DataFrame) -> list[HazardResult]:
        if not self.allow_network:
            return self.unavailable(len(points), "network lookups disabled")
        cgs_cache = PointCache("cgs_ms48_pga_2pc50")
        usgs_cache = PointCache("usgs_designmaps_asce7_16")
        out = []
        cgs_failures = 0
        for r in points.itertuples():
            lon, lat = float(r.lon), float(r.lat)
            val: float | None = None
            src = "cgs_ms48_pga_2pc50"
            version = "NSHM2023-MS48"
            try:
                val = self._cgs(cgs_cache, lon, lat)
            except SourceUnavailableError as exc:
                cgs_failures += 1
                log.warning("cgs_pga_failed", error=str(exc))
            if val is None:
                try:
                    val = self._usgs(usgs_cache, lon, lat)
                    src, version = "usgs_designmaps_asce7_16", "ASCE7-16 (NSHM2014) v4.0.x"
                except SourceUnavailableError as exc:
                    log.warning("usgs_designmaps_failed", error=str(exc))
            if val is None:
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        src,
                        "unavailable",
                        method="point_service",
                        units="g",
                        probability_horizon=self.info.probability_horizon,
                        caveats=[
                            "No PGA value returned by CGS MS48 or USGS design-maps services for this location."
                        ],
                    )
                )
                continue
            out.append(
                HazardResult(
                    self.info.hazard_id,
                    self.info.name,
                    src,
                    "covered",
                    source_version=version,
                    classification=pga_class(val),
                    value=float(val),
                    units="g",
                    probability_horizon=self.info.probability_horizon,
                    confidence="high" if src.startswith("cgs") else "medium",
                    method="point_service",
                    details={
                        "classification_bins": "CATRIX screening bins (assumption): <0.2 Low, 0.2-0.4 Moderate, 0.4-0.6 High, >=0.6 Very High",
                        "site_condition": "model reference site (CGS MS48) or site class B/C uniform hazard (USGS pgauh)",
                    },
                )
            )
        cgs_cache.flush()
        usgs_cache.flush()
        return out


class FaultProximityModule(HazardModule):
    info = HazardInfo(
        hazard_id="earthquake_fault_proximity",
        name="Earthquake – distance to nearest Quaternary fault",
        description="Distance in metres to the nearest mapped Quaternary fault trace (CGS Fault Activity Map).",
        source_ids=("cgs_qfaults",),
        method="static_overlay",
        units="m",
        probability_horizon=None,
        scale_note="Context only. Fault proximity is not an earthquake risk model: shaking "
        "hazard is captured by PGA; surface-rupture zoning requires Alquist-Priolo maps.",
        map_layer_available=True,
    )

    def assess(self, session: Session, points: pd.DataFrame) -> list[HazardResult]:
        status, version = source_state(session, self.info.source_ids[0])
        if status != "ingested":
            return self.unavailable(len(points), f"fault layer not ingested (status={status})")
        near = nearest_line_distance(session, "fault", points)
        out = []
        for i in points.index:
            n = near.get(int(i))
            if n is None:
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        self.info.source_ids[0],
                        "not_in_hazard_area",
                        source_version=version,
                        method="static_overlay",
                        units="m",
                        caveats=["No mapped Quaternary fault within 100 km."],
                    )
                )
            else:
                d = n["distance_m"]
                cls = (
                    "within 1 km"
                    if d <= 1000
                    else ("1-5 km" if d <= 5000 else ("5-20 km" if d <= 20000 else ">20 km"))
                )
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        self.info.source_ids[0],
                        "covered",
                        source_version=version,
                        classification=cls,
                        value=round(d, 1),
                        units="m",
                        method="static_overlay",
                        confidence="high",
                        details={"nearest_fault": n["name"]},
                        caveats=[self.info.scale_note],
                    )
                )
        return out


class EarthquakeHistoryModule(HazardModule):
    info = HazardInfo(
        hazard_id="earthquake_history",
        name="Earthquake – historical seismicity (50 km)",
        description="Count of M4+ and M5.5+ earthquakes within 50 km since 1980 (USGS ComCat).",
        source_ids=("usgs_earthquake_catalog",),
        method="static_overlay",
        units="M4+ events within 50 km since 1980",
        probability_horizon=None,
        scale_note="Observed seismicity is context, not a forecast; PGA already integrates the source model.",
    )

    def assess(self, session: Session, points: pd.DataFrame) -> list[HazardResult]:
        status, version = source_state(session, self.info.source_ids[0])
        if status != "ingested":
            return self.unavailable(
                len(points), f"earthquake catalogue not ingested (status={status})"
            )
        stats = earthquake_history_lookup(session, points)
        out = []
        for i in points.index:
            s = stats.get(int(i), {})
            n4 = int(s.get("n_m4_50km") or 0)
            out.append(
                HazardResult(
                    self.info.hazard_id,
                    self.info.name,
                    self.info.source_ids[0],
                    "covered" if n4 else "not_in_hazard_area",
                    source_version=version,
                    classification=None,
                    value=float(n4),
                    units=self.info.units,
                    method="static_overlay",
                    confidence="high",
                    details={
                        "n_m5_5_plus_50km": int(s.get("n_m55_50km") or 0),
                        "max_magnitude_50km": s.get("max_mag_50km"),
                        "nearest_m5_5_plus_m": s.get("nearest_m55_m"),
                    },
                )
            )
        return out

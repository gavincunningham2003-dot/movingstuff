"""Secondary hazards: landslide, liquefaction, tsunami, extreme rainfall and county-level
regional benchmarks (windstorm, drought, extreme heat, coastal flooding, winter storm).

Each uses the most authoritative available layer; none is forced into a probabilistic
scale it does not have.
"""

from __future__ import annotations

import re
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from rasterio.transform import rowcol
from sqlalchemy.orm import Session

from catrix.geo.raster import open_raster
from catrix.hazards.base import HazardInfo, HazardModule, HazardResult, Method
from catrix.hazards.lookup import polygon_count, polygon_lookup, regional_lookup, source_state
from catrix.hazards.point_cache import PointCache
from catrix.ingest.arcgis import mapserver_identify_raster_batch
from catrix.ingest.http import SourceUnavailableError, download_file, get_text
from catrix.ingest.registry import load_registry
from catrix.ingest.store import rows
from catrix.logging import get_logger

log = get_logger(__name__)

LANDSLIDE_LABELS = {
    0: "Low (0)",
    3: "Low-moderate (3)",
    5: "Moderate (5)",
    6: "Moderate (6)",
    7: "Moderate-high (7)",
    8: "High (8)",
    9: "High (9)",
    10: "Very high (10)",
}


class LandslideSusceptibilityModule(HazardModule):
    info = HazardInfo(
        hazard_id="landslide",
        name="Landslide – deep-seated susceptibility (CGS MS58)",
        description="CGS Map Sheet 58 susceptibility class (0 low to 10 high) from rock strength and slope.",
        source_ids=("cgs_landslide_susceptibility_ms58",),
        method="raster_sample",
        units="class (0-10)",
        probability_horizon=None,
        scale_note="Relative susceptibility to deep-seated landslides (2011). Not a probability; shallow landslides and debris flows are not represented.",
    )

    def __init__(self, *, allow_network: bool = True):
        self.allow_network = allow_network

    def assess(self, session: Session, points: pd.DataFrame) -> list[HazardResult]:
        if not self.allow_network:
            return self.unavailable(len(points), "network lookups disabled")
        spec = load_registry().get(self.info.source_ids[0])
        assert spec.api_url
        cache = PointCache(self.info.source_ids[0])
        coords = [(float(r.lon), float(r.lat)) for r in rows(points)]

        # One multipoint identify per IDENTIFY_BATCH uncached points instead of one request
        # per address (the service is a raster behind a MapServer: no download, no query).
        missing = [c for c in coords if cache.get(*c) is None]
        unavailable: str | None = None
        if missing:
            try:
                attrs_list = mapserver_identify_raster_batch(
                    self.info.source_ids[0], spec.api_url, 0, missing
                )
            except SourceUnavailableError as exc:
                unavailable = str(exc)
            else:
                for (lon, lat), attrs in zip(missing, attrs_list, strict=True):
                    raw = (attrs or {}).get("Raster.Value") or (attrs or {}).get(
                        "UniqueValue.Pixel Value"
                    )
                    cls = None if raw in (None, "", "NoData") else int(float(str(raw)))
                    cache.put(lon, lat, {"class": cls})
        cache.flush()

        out = []
        for lon, lat in coords:
            cached = cache.get(lon, lat)
            if cached is None:
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        self.info.source_ids[0],
                        "unavailable",
                        method="raster_sample",
                        caveats=[unavailable or "lookup failed"],
                    )
                )
                continue
            cls = cached.get("class")
            if cls is None:
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        self.info.source_ids[0],
                        "outside_coverage",
                        method="raster_sample",
                        caveats=["NoData pixel (water or outside raster)."],
                    )
                )
            else:
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        self.info.source_ids[0],
                        "covered" if cls > 0 else "not_in_hazard_area",
                        source_version="MS58-2011",
                        classification=LANDSLIDE_LABELS.get(cls, f"class {cls}"),
                        value=float(cls),
                        units=self.info.units,
                        method="raster_sample",
                        confidence="medium",
                        caveats=[self.info.scale_note],
                    )
                )
        return out


class LiquefactionModule(HazardModule):
    info = HazardInfo(
        hazard_id="liquefaction",
        name="Liquefaction – CGS Seismic Hazard Zone",
        description="Whether the location lies within a CGS-designated liquefaction hazard zone.",
        source_ids=("cgs_seismic_hazard_zones",),
        method="static_overlay",
        units=None,
        probability_horizon=None,
        scale_note="Regulatory zone requiring site investigation. Zones exist only for evaluated "
        "quadrangles (mainly urban Southern California and the Bay Area); elsewhere the "
        "status is 'outside_coverage', never 'no hazard'.",
        map_layer_available=True,
    )

    def assess(self, session: Session, points: pd.DataFrame) -> list[HazardResult]:
        status, version = source_state(session, self.info.source_ids[0])
        if status != "ingested" or polygon_count(session, "liquefaction") == 0:
            return self.unavailable(
                len(points),
                "CGS Seismic Hazard Zones not ingested (local download required; see data_sources.yaml)",
            )
        hits = polygon_lookup(session, "liquefaction", points)
        out = []
        for i in points.index:
            h = hits.get(int(i))
            if h is None:
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        self.info.source_ids[0],
                        "outside_coverage",
                        source_version=version,
                        method="static_overlay",
                        confidence="low",
                        caveats=[
                            "Not within a mapped liquefaction zone; CATRIX cannot distinguish 'evaluated and not zoned' from 'never evaluated' without quadrangle coverage polygons."
                        ],
                    )
                )
            else:
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        self.info.source_ids[0],
                        "covered",
                        source_version=version,
                        classification="Liquefaction zone",
                        value=1.0,
                        units="in zone (1)",
                        method="static_overlay",
                        confidence="high",
                        details=h["attributes"],
                    )
                )
        return out


# Prefer a "Yes" polygon when a point sits in overlapping CGS polygons.
_TSUNAMI_PRIORITY = (
    "(CASE WHEN h.classification ILIKE 'yes%' THEN 0 ELSE 1 END), class_code DESC NULLS LAST"
)


def tsunami_polygon_is_hazard(hit: dict[str, Any]) -> bool:
    """True only for CGS polygons whose ``Evacuate`` / classification says the location is
    inside the tsunami hazard area ("Yes, Tsunami Hazard Area")."""
    label = (
        str((hit.get("attributes") or {}).get("Evacuate") or hit.get("classification") or "")
        .strip()
        .lower()
    )
    return label.startswith("yes")


class TsunamiModule(HazardModule):
    info = HazardInfo(
        hazard_id="tsunami",
        name="Tsunami – CGS hazard area",
        description="Whether the location lies within the CGS/Cal OES tsunami hazard (evacuation planning) area.",
        source_ids=("cgs_tsunami_hazard_area",),
        method="static_overlay",
        units=None,
        probability_horizon=None,
        scale_note="Maximum credible inundation for evacuation planning; not an annual probability or depth.",
        map_layer_available=True,
    )

    def assess(self, session: Session, points: pd.DataFrame) -> list[HazardResult]:
        status, version = source_state(session, self.info.source_ids[0])
        if status != "ingested" or polygon_count(session, "tsunami") == 0:
            return self.unavailable(len(points), f"tsunami layer not ingested (status={status})")
        hits = polygon_lookup(session, "tsunami", points, priority_sql=_TSUNAMI_PRIORITY)
        out = []
        for i in points.index:
            h = hits.get(int(i))
            if h is not None and not tsunami_polygon_is_hazard(h):
                # The CGS layer also publishes county-wide "No, Outside Hazard Area"
                # polygons; containment in one of those is evidence of *not* being exposed.
                h = None
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
                    )
                )
            else:
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        self.info.source_ids[0],
                        "covered",
                        source_version=version,
                        classification="Tsunami hazard area",
                        value=1.0,
                        units="in area (1)",
                        method="static_overlay",
                        confidence="high",
                        details={
                            "county": (h["attributes"] or {}).get("County"),
                            "label": (h["attributes"] or {}).get("Label"),
                        },
                        caveats=[self.info.scale_note],
                    )
                )
        return out


def _atlas14_grid_values(spec: Any, coords: list[tuple[float, float]]) -> list[float | None] | None:
    """Sample the Atlas 14 Volume 6 100-yr/24-h grid locally for every coordinate.

    Returns None when the grid cannot be obtained (caller falls back to the point
    service). Values are converted from thousandths of an inch to millimetres; NoData and
    out-of-grid points are None.
    """
    if not spec.download_url:
        return None
    raw_dir = spec.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    try:
        dl = download_file(
            spec.id, spec.download_url, dest=raw_dir / "sw100yr24ha.zip", expected_min_bytes=100_000
        )
        asc = raw_dir / "sw100yr24ha.asc"
        if not asc.exists():
            with zipfile.ZipFile(dl.path) as zf:
                zf.extractall(raw_dir)
        if not asc.exists():
            raise SourceUnavailableError(spec.id, spec.download_url, "zip has no .asc grid")
    except SourceUnavailableError as exc:
        log.warning("atlas14_grid_unavailable", error=str(exc))
        return None
    arr, transform, nodata = _load_grid(asc)
    out: list[float | None] = []
    for lon, lat in coords:
        row, col = rowcol(transform, lon, lat)
        if row < 0 or col < 0 or row >= arr.shape[0] or col >= arr.shape[1]:
            out.append(None)
            continue
        v = float(arr[row, col])
        out.append(
            None if (nodata is not None and v == nodata) or v < 0 else round(v / 1000.0 * 25.4, 1)
        )
    return out


@lru_cache(maxsize=2)
def _load_grid(path: Path) -> tuple[np.ndarray, Any, float | None]:
    """The Atlas 14 grid is an ASCII (.asc) raster: windowed reads re-scan the text file,
    so read it once (~3.6M cells, 15 MB) and index the array directly."""
    with open_raster(path) as ds:
        return ds.read(1), ds.transform, ds.nodata


class ExtremeRainfallModule(HazardModule):
    info = HazardInfo(
        hazard_id="extreme_rainfall",
        name="Extreme rainfall – 100-year 24-hour depth (NOAA Atlas 14)",
        description="Precipitation-frequency estimate: 24-hour depth with 1% annual exceedance probability.",
        source_ids=("noaa_atlas14",),
        method="point_service",
        units="mm",
        probability_horizon="1% annual exceedance (24-hour duration)",
        scale_note="Rainfall intensity hazard, not flood damage. Atlas 14 Vol. 6 is dated 2011/2014.",
    )

    def __init__(self, *, allow_network: bool = True):
        self.allow_network = allow_network

    @staticmethod
    def parse_atlas14(text: str) -> float | None:
        """Extract the 24-hr / 100-yr value from the Atlas 14 text response.

        The service returns a JavaScript-style ``quantiles = [[...],...]`` block where rows
        are durations (5-min ... 60-day) and columns are return periods (1,2,5,10,25,50,100,
        200,500,1000 yr). The 24-hr row is index 9 and 100-yr column index 6.
        """
        m = re.search(r"quantiles\s*=\s*(\[\[.*?\]\])", text, re.S)
        if not m:
            return None
        try:
            rows = [
                [float(v.strip("' \"")) for v in re.findall(r"'([^']*)'", row)]
                for row in re.findall(r"\[([^\[\]]*)\]", m.group(1))
            ]
            return rows[9][6]
        except (IndexError, ValueError):
            return None

    def assess(self, session: Session, points: pd.DataFrame) -> list[HazardResult]:
        spec = load_registry().get(self.info.source_ids[0])
        coords = [(float(r.lon), float(r.lat)) for r in rows(points)]

        # Preferred path: the downloadable Volume 6 grid, sampled locally (one 3.6 MB file
        # replaces one web request per address). Results are written to the same point
        # cache the service path uses, so both paths share it.
        cache = PointCache(self.info.source_ids[0])
        missing = [c for c in coords if cache.get(*c) is None]
        grid_vals = _atlas14_grid_values(spec, missing) if missing else []
        method: Method = "raster_sample"
        if grid_vals is not None:
            for (lon, lat), v in zip(missing, grid_vals, strict=True):
                cache.put(lon, lat, {"depth_mm": v})
            cache.flush()
        elif not self.allow_network:
            return self.unavailable(len(points), "network lookups disabled")
        else:
            method = "point_service"
            assert spec.api_url

        out = []
        for lon, lat in coords:
            cached = cache.get(lon, lat)
            try:
                if cached is None:
                    assert spec.api_url is not None
                    txt = get_text(self.info.source_ids[0], spec.api_url.format(lat=lat, lon=lon))
                    val = self.parse_atlas14(txt)
                    cache.put(lon, lat, {"depth_mm": val})
                else:
                    val = cached.get("depth_mm")
            except SourceUnavailableError as exc:
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        self.info.source_ids[0],
                        "unavailable",
                        method=method,
                        caveats=[str(exc)],
                    )
                )
                continue
            if val is None:
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        self.info.source_ids[0],
                        "unavailable",
                        method=method,
                        caveats=[
                            "Atlas 14 response could not be parsed (service format may have changed)."
                        ],
                    )
                )
            else:
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        self.info.source_ids[0],
                        "covered",
                        source_version="Atlas14-Vol6",
                        value=round(val, 1),
                        units="mm",
                        probability_horizon=self.info.probability_horizon,
                        method=method,
                        confidence="medium",
                        caveats=[self.info.scale_note],
                    )
                )
        cache.flush()
        return out


class RegionalIndexModule(HazardModule):
    """County-level FEMA NRI benchmark for hazards without a property-level layer."""

    def __init__(self, hazard_id: str, name: str, nri_key: str, description: str):
        self.info = HazardInfo(
            hazard_id=hazard_id,
            name=name,
            description=description,
            source_ids=("fema_nri_counties",),
            method="regional_index",
            units="NRI risk score (county, 0-100)",
            probability_horizon=None,
            scale_note="County-level FEMA National Risk Index score: expected-annual-loss-based relative ranking across US counties. Regional context only, not property-level hazard.",
        )
        self.nri_key = nri_key

    def assess(self, session: Session, points: pd.DataFrame) -> list[HazardResult]:
        status, version = source_state(session, "fema_nri_counties")
        if status != "ingested":
            return self.unavailable(len(points), f"FEMA NRI not ingested (status={status})")
        if "county_fips" not in points.columns:
            return self.unavailable(len(points), "county_fips not available for regional lookup")
        metric = f"{self.nri_key}_risk_score"
        vals = regional_lookup(
            session,
            [metric, f"{self.nri_key}_eal_total_usd"],
            points["county_fips"].dropna().unique(),
        )
        out = []
        for r in rows(points):
            fips = getattr(r, "county_fips", None)
            v = (vals.get(fips) or {}).get(metric) if fips else None
            if v is None or v.get("value") is None:
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        "fema_nri_counties",
                        "outside_coverage",
                        source_version=version,
                        method="regional_index",
                        caveats=["No NRI value for this county."],
                    )
                )
            else:
                eal = (vals.get(fips or "") or {}).get(f"{self.nri_key}_eal_total_usd", {})
                out.append(
                    HazardResult(
                        self.info.hazard_id,
                        self.info.name,
                        "fema_nri_counties",
                        "covered",
                        source_version=v.get("version") or version,
                        classification=v.get("rating"),
                        value=float(v["value"]),
                        units=self.info.units,
                        method="regional_index",
                        confidence="low",
                        details={"county_fips": fips, "county_eal_total_usd": eal.get("value")},
                        caveats=[self.info.scale_note],
                    )
                )
        return out

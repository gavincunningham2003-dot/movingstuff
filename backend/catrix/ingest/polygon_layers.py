"""Generic ArcGIS polygon-layer connectors → ``hazard_geometries``.

Each entry in :data:`LAYERS` maps a registry source to a ``hazard_id`` and says which
attribute carries the classification. The FEMA NFHL layer is included with the published
schema but is marked ``unverified`` in the registry (host unreachable from the dev
environment on 2026-10-13) — a failure is reported, never masked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import geopandas as gpd
import pandas as pd
import pyogrio
from sqlalchemy import delete
from sqlalchemy.orm import Session

from catrix.config import GEOGRAPHIC_CRS
from catrix.db.models import HazardGeometry
from catrix.ingest.arcgis import fetch_layer
from catrix.ingest.base import IngestResult
from catrix.ingest.http import SourceUnavailableError
from catrix.ingest.registry import load_registry, mark_failed, mark_ingested
from catrix.ingest.store import clean_polygons, write_parquet
from catrix.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class PolygonLayerSpec:
    source_id: str
    hazard_id: str
    fields: list[str]
    class_field: str
    code_field: str | None = None
    where: str = "1=1"
    local_glob: str | None = None  # accept a local GDB/shapefile/geojson instead of REST
    local_layer: str | None = None


LAYERS: dict[str, PolygonLayerSpec] = {
    "wildfire_fhsz": PolygonLayerSpec(
        source_id="calfire_fhsz_2024",
        hazard_id="wildfire_fhsz",
        fields=["OBJECTID", "FHSZ", "FHSZ_Description", "FHSZ_7Class", "SRA22_2"],
        class_field="FHSZ_Description",
        code_field="FHSZ",
    ),
    "tsunami": PolygonLayerSpec(
        source_id="cgs_tsunami_hazard_area",
        hazard_id="tsunami",
        fields=["OBJECTID", "Evacuate", "County", "Label"],
        class_field="Evacuate",
        # The service also serves county-wide "No, Outside Hazard Area" polygons; storing
        # them made every point inside them look exposed. Keep only the hazard area.
        where="Evacuate LIKE 'Yes%'",
    ),
    "flood_nfhl": PolygonLayerSpec(
        source_id="fema_nfhl",
        hazard_id="flood_nfhl",
        fields=[
            "OBJECTID",
            "DFIRM_ID",
            "FLD_AR_ID",
            "FLD_ZONE",
            "ZONE_SUBTY",
            "SFHA_TF",
            "STATIC_BFE",
        ],
        class_field="FLD_ZONE",
        local_glob="*.gdb",
        local_layer="S_FLD_HAZ_AR",
    ),
    "liquefaction": PolygonLayerSpec(
        source_id="cgs_seismic_hazard_zones",
        hazard_id="liquefaction",
        # CGS publishes the official zones as one feature service per zone type on ArcGIS
        # Online; every polygon in this layer is a liquefaction zone, attributed by the
        # quadrangle it belongs to and its release date.
        fields=["OBJECTID", "QUAD_NAME", "RELEASED", "REVISED"],
        class_field="QUAD_NAME",
        local_glob="*.shp",
    ),
}


def _load_local(
    spec: PolygonLayerSpec, bbox: tuple[float, float, float, float] | None
) -> gpd.GeoDataFrame | None:
    src = load_registry().get(spec.source_id)
    raw_dir = src.raw_dir
    if not raw_dir.exists() or spec.local_glob is None:
        return None
    paths = sorted(raw_dir.glob(spec.local_glob)) + sorted(
        raw_dir.glob(f"*{spec.local_glob[1:]}.zip")
    )
    if not paths:
        return None
    frames = []
    for p in paths:
        kwargs: dict[str, Any] = {}
        if spec.local_layer:
            kwargs["layer"] = spec.local_layer
        if bbox:
            kwargs["bbox"] = bbox
        path = f"zip://{p}" if p.suffix == ".zip" else str(p)
        frames.append(pyogrio.read_dataframe(path, **kwargs))
    gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True))
    return gdf.to_crs(GEOGRAPHIC_CRS)


class PolygonLayerConnector:
    def __init__(self, layer_key: str):
        self.spec = LAYERS[layer_key]
        self.source_id = self.spec.source_id

    def run(
        self,
        session: Session,
        *,
        force: bool = False,
        bbox: tuple[float, float, float, float] | None = None,
        **_: Any,
    ) -> IngestResult:
        spec = self.spec
        src = load_registry().get(spec.source_id)
        total = 0
        dropped_total = 0
        parts: list[gpd.GeoDataFrame] = []
        session.execute(delete(HazardGeometry).where(HazardGeometry.hazard_id == spec.hazard_id))

        def persist(page: gpd.GeoDataFrame) -> None:
            nonlocal total, dropped_total
            if len(page) == 0:
                return
            if spec.class_field not in page.columns:
                raise SourceUnavailableError(
                    spec.source_id,
                    src.api_url or str(src.raw_dir),
                    f"missing class field {spec.class_field}",
                )
            page, dropped = clean_polygons(page)
            dropped_total += dropped
            attr_cols = [c for c in page.columns if c != "geometry"]
            rows = []
            for r in page.itertuples(index=False):
                rec = dict(zip(page.columns, r, strict=True))
                geom = rec.pop("geometry")
                code = rec.get(spec.code_field) if spec.code_field else None
                oid = rec.get("OBJECTID")
                rows.append(
                    {
                        "source_id": spec.source_id,
                        "hazard_id": spec.hazard_id,
                        "source_object_id": str(oid) if oid is not None else None,
                        "classification": (
                            str(rec.get(spec.class_field))
                            if rec.get(spec.class_field) is not None
                            else None
                        ),
                        "class_code": int(float(str(code)))
                        if code is not None and pd.notna(code)
                        else None,
                        "attributes": {
                            k: (None if (isinstance(v, float) and pd.isna(v)) else v)
                            for k, v in rec.items()
                            if k in attr_cols
                        },
                        "geom": f"SRID=4326;{geom.wkt}",
                    }
                )
            session.bulk_insert_mappings(HazardGeometry, rows)
            session.flush()
            total += len(rows)
            parts.append(page)

        try:
            local = _load_local(spec, bbox)
            if local is not None:
                persist(local)
                how = "local"
            else:
                if src.verification_status == "local_only" or not src.api_url:
                    raise SourceUnavailableError(
                        spec.source_id,
                        src.url or "",
                        f"no REST endpoint; place files matching {spec.local_glob} in {src.raw_dir}",
                    )
                fetch_layer(
                    spec.source_id,
                    src.api_url,
                    where=spec.where,
                    out_fields=spec.fields or None,
                    geometry_bbox=bbox,
                    batch_callback=persist,
                    page_size=src.page_size,
                )
                how = "arcgis"
        except SourceUnavailableError as exc:
            session.rollback()
            mark_failed(session, spec.source_id, str(exc), unavailable=True)
            return IngestResult(spec.source_id, "unavailable", message=str(exc))

        if parts:
            write_parquet(
                gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=GEOGRAPHIC_CRS),
                f"hazard_{spec.hazard_id}",
            )
        version = f"{how}-{datetime.now(UTC).date().isoformat()}"
        quality = {"n_invalid_geometries_dropped": dropped_total, "bbox": bbox, "acquisition": how}
        mark_ingested(session, spec.source_id, record_count=total, version=version, extra=quality)
        return IngestResult(spec.source_id, "ingested", total, version, quality=quality)

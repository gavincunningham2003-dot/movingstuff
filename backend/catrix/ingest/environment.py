"""Derived environmental features per address (distance to faults, fire-history
summaries, optional land-cover / fuel rasters from local files)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from catrix.db.models import Address
from catrix.geo.raster import open_raster, sample_categorical
from catrix.hazards.lookup import fire_history_lookup, nearest_line_distance
from catrix.ingest.base import IngestResult
from catrix.ingest.elevation import upsert_observation
from catrix.ingest.registry import load_registry
from catrix.logging import get_logger

log = get_logger(__name__)

NLCD_CLASSES = {
    11: "Open water",
    12: "Perennial ice/snow",
    21: "Developed, open space",
    22: "Developed, low intensity",
    23: "Developed, medium intensity",
    24: "Developed, high intensity",
    31: "Barren land",
    41: "Deciduous forest",
    42: "Evergreen forest",
    43: "Mixed forest",
    52: "Shrub/scrub",
    71: "Grassland/herbaceous",
    81: "Pasture/hay",
    82: "Cultivated crops",
    90: "Woody wetlands",
    95: "Emergent herbaceous wetlands",
}


def _first_raster(source_id: str, pattern: str = "*.tif") -> Path | None:
    d = load_registry().get(source_id).raw_dir
    if not d.exists():
        return None
    # Newest versioned sub-directory first (landfire_fuels/LF2024, mrlc_nlcd/2023), then
    # any raster in the directory root.
    sub = sorted((x for x in d.iterdir() if x.is_dir()), key=lambda x: x.name)
    for vd in reversed(sub):
        files = sorted(vd.glob(pattern))
        if files:
            return files[-1]
    files = sorted(d.glob(pattern))
    return files[-1] if files else None


class EnvironmentConnector:
    """Not a download connector: derives features from already-ingested layers."""

    source_id = "derived_environment"

    def run(self, session: Session, *, force: bool = False, **_: Any) -> IngestResult:
        rows = session.execute(select(Address.id, Address.lon, Address.lat)).all()
        if not rows:
            return IngestResult(self.source_id, "failed", message="no addresses")
        df = pd.DataFrame(rows, columns=["address_id", "lon", "lat"]).set_index("address_id")
        faults = nearest_line_distance(session, "fault", df)
        fires = fire_history_lookup(session, df, since_year=1990, radius_m=5000)
        fires_1km = fire_history_lookup(session, df, since_year=1900, radius_m=1000)
        nlcd = _first_raster("mrlc_nlcd")
        lf = _first_raster("landfire_fuels")
        n = 0
        nlcd_ds = open_raster(nlcd) if nlcd else None
        lf_ds = open_raster(lf) if lf else None
        try:
            for aid_raw, r in df.iterrows():
                aid = int(str(aid_raw))
                fields: dict[str, Any] = {"sources": {}}
                f = faults.get(aid)
                if f:
                    fields["distance_to_fault_m"] = f["distance_m"]
                    fields["sources"]["fault"] = "cgs_qfaults"
                fh = fires.get(aid, {})
                fields["fires_within_5km_since_1990"] = int(fh.get("n_fires_radius") or 0)
                f1 = fires_1km.get(aid, {})
                last = f1.get("last_fire_year")
                fields["years_since_last_fire_within_1km"] = (
                    float(pd.Timestamp.utcnow().year - last) if last else None
                )
                fields["sources"]["fire_history"] = "calfire_fire_perimeters"
                if nlcd_ds is not None:
                    code = sample_categorical(nlcd_ds, float(r.lon), float(r.lat))
                    fields["land_cover_code"] = code
                    fields["land_cover_class"] = (
                        NLCD_CLASSES.get(code) if code is not None else None
                    )
                    fields["sources"]["land_cover"] = f"mrlc_nlcd:{nlcd.name}"  # type: ignore[union-attr]
                if lf_ds is not None:
                    fm = sample_categorical(lf_ds, float(r.lon), float(r.lat))
                    fields["fuel_model"] = f"FBFM40-{fm}" if fm is not None else None
                    fields["sources"]["fuel"] = f"landfire_fuels:{lf.name}"  # type: ignore[union-attr]
                upsert_observation(session, aid, **fields)
                n += 1
                if n % 2000 == 0:
                    session.flush()
                    log.info("environment_progress", n=n)
        finally:
            if nlcd_ds is not None:
                nlcd_ds.close()
            if lf_ds is not None:
                lf_ds.close()
        session.flush()
        return IngestResult(
            self.source_id,
            "ingested",
            n,
            quality={
                "nlcd_file": nlcd.name if nlcd else None,
                "landfire_file": lf.name if lf else None,
            },
        )

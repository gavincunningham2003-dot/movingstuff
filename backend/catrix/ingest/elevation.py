"""USGS 3DEP elevation, slope and aspect → ``environmental_observations``.

Strategy: group addresses by 1x1-degree tile, download each needed tile once (≈28 MB), and
sample a 3x3 window per address. If a tile cannot be downloaded the addresses in it fall
back to the EPQS point service for elevation only (slope/aspect stay null and the status
is recorded), or are left null when that also fails.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from catrix.db.models import Address, EnvironmentalObservation
from catrix.geo.raster import dem_tile_id, open_raster, sample_terrain
from catrix.ingest.base import IngestResult
from catrix.ingest.http import SourceUnavailableError, download_file, get_json
from catrix.ingest.registry import load_registry, mark_failed, mark_ingested
from catrix.ingest.store import write_parquet
from catrix.logging import get_logger

log = get_logger(__name__)

SOURCE_ID = "usgs_3dep_1as"
EPQS_ID = "usgs_epqs"


def epqs_elevation(lon: float, lat: float) -> float | None:
    spec = load_registry().get(EPQS_ID)
    assert spec.api_url
    data = get_json(EPQS_ID, spec.api_url.format(lon=lon, lat=lat))
    val = data.get("value")
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    return None if v < -1000 or v > 9000 else v


def upsert_observation(session: Session, address_id: int, **fields: Any) -> None:
    obs = session.get(EnvironmentalObservation, address_id)
    if obs is None:
        obs = EnvironmentalObservation(address_id=address_id, sources={})
        session.add(obs)
    for k, v in fields.items():
        if k == "sources":
            merged = dict(obs.sources or {})
            merged.update(v)
            obs.sources = merged
        else:
            setattr(obs, k, v)
    obs.computed_at = datetime.now(UTC)


class ElevationConnector:
    source_id = SOURCE_ID

    def run(
        self, session: Session, *, force: bool = False, max_tiles: int = 40, **_: Any
    ) -> IngestResult:
        spec = load_registry().get(SOURCE_ID)
        assert spec.download_pattern
        rows = session.execute(select(Address.id, Address.lon, Address.lat)).all()
        if not rows:
            msg = "no addresses ingested"
            mark_failed(session, SOURCE_ID, msg)
            return IngestResult(SOURCE_ID, "failed", message=msg)
        by_tile: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
        for aid, lon, lat in rows:
            by_tile[dem_tile_id(lon, lat)].append((aid, lon, lat))
        if len(by_tile) > max_tiles:
            log.warning("too_many_tiles", n=len(by_tile), max=max_tiles)
        status_counts: dict[str, int] = defaultdict(int)
        records = []
        tiles_ok = 0
        tiles_failed: list[str] = []
        for i, (tile, pts) in enumerate(sorted(by_tile.items())):
            if i >= max_tiles:
                status_counts["skipped_tile_cap"] += len(pts)
                continue
            url = spec.download_pattern.format(tile=tile)
            try:
                dl = download_file(
                    SOURCE_ID,
                    url,
                    dest=spec.raw_dir / f"USGS_1_{tile}.tif",
                    force=force,
                    expected_min_bytes=1_000_000,
                )
            except SourceUnavailableError as exc:
                log.error("dem_tile_unavailable", tile=tile, error=str(exc))
                tiles_failed.append(tile)
                for aid, lon, lat in pts:
                    try:
                        elev = epqs_elevation(lon, lat)
                        status = "epqs_fallback" if elev is not None else "unavailable"
                    except SourceUnavailableError:
                        elev, status = None, "unavailable"
                    status_counts[status] += 1
                    records.append(
                        {
                            "address_id": aid,
                            "elevation_m": elev,
                            "slope_deg": None,
                            "aspect_deg": None,
                            "status": status,
                            "source": EPQS_ID if elev is not None else None,
                        }
                    )
                continue
            tiles_ok += 1
            with open_raster(dl.path) as ds:
                for aid, lon, lat in pts:
                    s = sample_terrain(ds, lon, lat)
                    status_counts[s.status] += 1
                    records.append(
                        {
                            "address_id": aid,
                            "elevation_m": s.elevation_m,
                            "slope_deg": s.slope_deg,
                            "aspect_deg": s.aspect_deg,
                            "status": s.status,
                            "source": SOURCE_ID if s.elevation_m is not None else None,
                        }
                    )
            log.info("dem_tile_sampled", tile=tile, n=len(pts))
        for r in records:
            upsert_observation(
                session,
                r["address_id"],
                elevation_m=r["elevation_m"],
                slope_deg=r["slope_deg"],
                aspect_deg=r["aspect_deg"],
                sources={"terrain": r["source"] or "unavailable", "terrain_status": r["status"]},
            )
        session.flush()
        write_parquet(pd.DataFrame(records), "terrain_samples")
        quality = {
            "tiles_ok": tiles_ok,
            "tiles_failed": tiles_failed,
            "status_counts": dict(status_counts),
        }
        n = sum(1 for r in records if r["elevation_m"] is not None)
        mark_ingested(session, SOURCE_ID, record_count=n, version="3DEP-1as-current", extra=quality)
        return IngestResult(
            SOURCE_ID, "ingested" if n else "failed", n, "3DEP-1as-current", quality=quality
        )

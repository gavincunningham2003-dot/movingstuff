"""OpenAddresses California address points → ``addresses``.

Two acquisition paths:

1. **Batch API** (requires ``CATRIX_OPENADDRESSES_TOKEN``): lists ``us/ca/*`` sources from
   ``/api/data``, downloads each selected job's ``source.geojson.gz``.
2. **Local files**: any ``*.geojson``, ``*.geojson.gz`` or ``*.csv`` dropped into
   ``data/raw/openaddresses_ca/``. OpenAddresses' two historical schemas are supported:
   GeoJSON features with ``number/street/unit/city/district/region/postcode/id/hash``
   properties, and the legacy CSV with ``LON,LAT,NUMBER,STREET,...``.

The connector then validates coordinates, deduplicates, draws a stratified sample across
counties (so the demonstration is not dominated by Los Angeles) and assigns a county by
point-in-polygon against ``regions``.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from catrix.config import GEOGRAPHIC_CRS, get_settings
from catrix.db.models import Address, Region
from catrix.geo.spatial import point_in_polygon_join, points_gdf
from catrix.geo.validation import check_coordinates, dedup_key
from catrix.ingest.base import IngestResult
from catrix.ingest.http import SourceUnavailableError, download_file, get_json
from catrix.ingest.registry import load_registry, mark_failed, mark_ingested
from catrix.ingest.store import write_parquet
from catrix.logging import get_logger

log = get_logger(__name__)

SOURCE_ID = "openaddresses_ca"
GEOJSON_FIELDS = [
    "number",
    "street",
    "unit",
    "city",
    "district",
    "region",
    "postcode",
    "id",
    "hash",
]
CSV_FIELDS = [
    "LON",
    "LAT",
    "NUMBER",
    "STREET",
    "UNIT",
    "CITY",
    "DISTRICT",
    "REGION",
    "POSTCODE",
    "ID",
    "HASH",
]

# Default demonstration sample: a spread of coastal, valley, foothill and desert counties.
DEFAULT_COUNTIES = [
    "us/ca/alameda",
    "us/ca/butte",
    "us/ca/contra_costa",
    "us/ca/el_dorado",
    "us/ca/fresno",
    "us/ca/los_angeles",
    "us/ca/marin",
    "us/ca/napa",
    "us/ca/orange",
    "us/ca/riverside",
    "us/ca/sacramento",
    "us/ca/san_bernardino",
    "us/ca/san_diego",
    "us/ca/santa_barbara",
    "us/ca/santa_cruz",
    "us/ca/shasta",
    "us/ca/sonoma",
    "us/ca/ventura",
]


# --------------------------------------------------------------------------- parsing
def _parse_geojson_lines(path: Path) -> pd.DataFrame:
    opener = gzip.open if path.suffix == ".gz" else open
    rows: list[dict[str, Any]] = []
    with opener(path, "rt", encoding="utf-8") as fh:  # type: ignore[operator]
        first = fh.read(1)
        fh.seek(0)
        if first == "{" and '"FeatureCollection"' in fh.read(200):
            fh.seek(0)
            data = json.load(fh)
            feats = data.get("features", [])
        else:
            fh.seek(0)
            feats = (json.loads(line) for line in fh if line.strip())
        for f in feats:
            props = f.get("properties") or {}
            geom = f.get("geometry") or {}
            coords = geom.get("coordinates") if geom.get("type") == "Point" else None
            rows.append(
                {
                    "lon": coords[0] if coords else None,
                    "lat": coords[1] if coords else None,
                    **{k: props.get(k) for k in GEOJSON_FIELDS},
                }
            )
    df = pd.DataFrame(rows)
    return df


def _parse_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in ("LON", "LAT", "NUMBER", "STREET") if c not in df.columns]
    if missing:
        raise SourceUnavailableError(SOURCE_ID, str(path), f"CSV missing columns {missing}")
    out = pd.DataFrame(
        {
            "lon": pd.to_numeric(df["LON"], errors="coerce"),
            "lat": pd.to_numeric(df["LAT"], errors="coerce"),
            "number": df.get("NUMBER"),
            "street": df.get("STREET"),
            "unit": df.get("UNIT"),
            "city": df.get("CITY"),
            "district": df.get("DISTRICT"),
            "region": df.get("REGION"),
            "postcode": df.get("POSTCODE"),
            "id": df.get("ID"),
            "hash": df.get("HASH"),
        }
    )
    return out


def parse_openaddresses_file(path: Path) -> pd.DataFrame:
    name = path.name.lower()
    if name.endswith(".csv"):
        df = _parse_csv(path)
    elif name.endswith((".geojson", ".geojson.gz", ".json", ".json.gz", ".ndjson")):
        df = _parse_geojson_lines(path)
    else:
        raise SourceUnavailableError(SOURCE_ID, str(path), "unsupported file type")
    df["source_file"] = path.name
    df["number"] = df["number"].astype("string").str.strip()
    df["street"] = df["street"].astype("string").str.strip()
    return df


# --------------------------------------------------------------------------- acquisition
def list_california_sources() -> list[dict[str, Any]]:
    spec = load_registry().get(SOURCE_ID)
    assert spec.api_url
    data = get_json(SOURCE_ID, spec.api_url)
    if not isinstance(data, list):
        raise SourceUnavailableError(SOURCE_ID, spec.api_url, "unexpected listing format")
    return [d for d in data if d.get("layer") == "addresses" and d.get("output", {}).get("output")]


def acquire_files(counties: list[str] | None, force: bool) -> tuple[list[Path], str]:
    """Return local files to parse and a description of how they were acquired."""
    settings = get_settings()
    spec = load_registry().get(SOURCE_ID)
    raw_dir = spec.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    local = sorted(
        p
        for p in raw_dir.iterdir()
        if p.is_file() and p.suffix.lower() in (".csv", ".gz", ".geojson", ".json")
    )
    if local and not force:
        return local, f"local files in {raw_dir}"

    if not settings.openaddresses_token:
        raise SourceUnavailableError(
            SOURCE_ID,
            spec.api_url or "",
            "no local OpenAddresses files found and CATRIX_OPENADDRESSES_TOKEN is not set "
            "(batch downloads return HTTP 403 without a token; create a free account at "
            f"https://batch.openaddresses.io or copy source files into {raw_dir})",
        )
    listing = list_california_sources()
    wanted = set(counties or DEFAULT_COUNTIES)
    chosen = [d for d in listing if d["source"] in wanted]
    missing = wanted - {d["source"] for d in chosen}
    if missing:
        log.warning("openaddresses_sources_missing", missing=sorted(missing))
    files: list[Path] = []
    assert spec.download_pattern
    for d in chosen:
        url = spec.download_pattern.format(job_id=d["job"])
        dest = raw_dir / f"{d['source'].replace('/', '_')}_{d['job']}.geojson.gz"
        dl = download_file(
            SOURCE_ID,
            url,
            dest=dest,
            force=force,
            headers={"Authorization": f"Bearer {settings.openaddresses_token}"},
            expected_min_bytes=1000,
        )
        files.append(dl.path)
    return files, "OpenAddresses batch API"


# --------------------------------------------------------------------------- processing
def stratified_sample(
    df: pd.DataFrame, n_total: int, seed: int, group_col: str = "source_file"
) -> pd.DataFrame:
    """Sample up to ``n_total`` rows spread evenly across groups (counties/files)."""
    if len(df) <= n_total:
        return df
    groups = df[group_col].fillna("unknown")
    sizes = groups.value_counts()
    per_group = n_total // max(len(sizes), 1)
    rng = np.random.default_rng(seed)
    parts = []
    leftover = 0
    for g, size in sizes.sort_values().items():
        take = min(size, per_group + leftover)
        leftover = per_group + leftover - take
        idx = df.index[groups == g]
        parts.append(df.loc[rng.choice(idx, size=take, replace=False)])
    out = pd.concat(parts)
    return out.sort_index()


def assign_counties(df: pd.DataFrame, counties: gpd.GeoDataFrame) -> pd.Series:
    pts = points_gdf(df)
    joined = point_in_polygon_join(pts, counties[["fips", "geometry"]], ["fips"])
    return joined["fips"].reindex(df.index)


class OpenAddressesConnector:
    source_id = SOURCE_ID

    def run(
        self,
        session: Session,
        *,
        force: bool = False,
        counties: list[str] | None = None,
        sample_size: int | None = None,
        **_: Any,
    ) -> IngestResult:
        settings = get_settings()
        n_target = sample_size or settings.address_sample_size
        try:
            files, how = acquire_files(counties, force)
        except SourceUnavailableError as exc:
            mark_failed(session, SOURCE_ID, str(exc), unavailable=True)
            return IngestResult(SOURCE_ID, "unavailable", message=str(exc))

        frames = []
        for f in files:
            try:
                frames.append(parse_openaddresses_file(f))
            except SourceUnavailableError as exc:
                log.error("openaddresses_parse_failed", file=str(f), error=str(exc))
        if not frames:
            msg = "no parseable OpenAddresses files"
            mark_failed(session, SOURCE_ID, msg)
            return IngestResult(SOURCE_ID, "failed", message=msg)
        df = pd.concat(frames, ignore_index=True)
        n_raw = len(df)

        # Validation & cleaning --------------------------------------------------
        check = check_coordinates(df, require_california=True)
        df = df[check.valid_mask].copy()
        df = df[df["number"].fillna("").str.len() > 0]
        df = df[df["street"].fillna("").str.len() > 0]
        df["dedup_key"] = [
            dedup_key(la, lo) + "|" + f"{(n or '').upper()}|{(s or '').upper()}|{(u or '').upper()}"
            for la, lo, n, s, u in zip(
                df["lat"], df["lon"], df["number"], df["street"], df["unit"].fillna(""), strict=True
            )
        ]
        n_before_dedup = len(df)
        df = df.drop_duplicates(subset="dedup_key")
        n_dupes = n_before_dedup - len(df)
        if "hash" in df.columns:
            df = df.drop_duplicates(subset=["hash"], keep="first")

        df = stratified_sample(df, n_target, settings.random_seed)

        # County assignment ----------------------------------------------------------
        regions = gpd.read_postgis(
            select(Region.fips, Region.geom).where(Region.region_type == "county"),
            session.connection(),
            geom_col="geom",
            crs=GEOGRAPHIC_CRS,
        ).rename_geometry("geometry")
        if len(regions) == 0:
            msg = "regions table is empty; run the counties connector first"
            mark_failed(session, SOURCE_ID, msg)
            return IngestResult(SOURCE_ID, "failed", message=msg)
        df["county_fips"] = assign_counties(df, regions).values
        n_outside = int(df["county_fips"].isna().sum())
        df = df[df["county_fips"].notna()]

        # Persist ----------------------------------------------------------------
        session.execute(delete(Address).where(Address.source_id == SOURCE_ID))
        now = datetime.now(UTC)
        rows = [
            {
                "external_id": (r.id if isinstance(r.id, str) and r.id else None),
                "source_id": SOURCE_ID,
                "source_file": r.source_file,
                "hash": r.hash if isinstance(r.hash, str) and r.hash else None,
                "number": r.number,
                "street": r.street,
                "unit": r.unit or None,
                "city": r.city or None,
                "district": r.district or None,
                "region": r.region or None,
                "postcode": str(r.postcode) if r.postcode else None,
                "lat": float(r.lat),
                "lon": float(r.lon),
                "county_fips": r.county_fips,
                "acquired_at": now,
                "geom": f"SRID=4326;POINT({float(r.lon)} {float(r.lat)})",
                "dedup_key": r.dedup_key,
            }
            for r in df.itertuples()
        ]
        # Rows without a hash cannot violate the (source_id, hash) uniqueness constraint.
        session.bulk_insert_mappings(Address, rows)
        session.flush()
        write_parquet(df.drop(columns=["dedup_key"]), "addresses")

        quality = {
            "n_raw": n_raw,
            "n_missing_coords": check.n_missing,
            "n_impossible_coords": check.n_impossible,
            "n_outside_california_bbox": check.n_outside_california,
            "n_zero_coords": check.n_zero,
            "n_duplicates_removed": n_dupes,
            "n_outside_counties": n_outside,
            "n_sampled": len(df),
            "acquisition": how,
            "files": [f.name for f in files],
        }
        mark_ingested(
            session,
            SOURCE_ID,
            record_count=len(rows),
            version=now.date().isoformat(),
            extra=quality,
        )
        return IngestResult(
            SOURCE_ID, "ingested", len(rows), now.date().isoformat(), quality=quality
        )

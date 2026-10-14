"""Persistence helpers shared by connectors: PostGIS writes, Parquet snapshots, batching."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, TypeVar, cast

import geopandas as gpd
import pandas as pd
from shapely.geometry import MultiLineString, MultiPolygon
from shapely.geometry.base import BaseGeometry
from sqlalchemy.orm import Session

from catrix.config import GEOGRAPHIC_CRS, get_settings
from catrix.geo.crs import ensure_crs
from catrix.logging import get_logger

log = get_logger(__name__)
T = TypeVar("T")


def rows(df: pd.DataFrame) -> Iterator[Any]:
    """``df.itertuples()`` with ``Any``-typed fields. pandas-stubs types every namedtuple
    field as the union of all possible column dtypes, which turns ``float(r.lon)`` into a
    type error; the fields are read from frames whose columns the caller just built."""
    return cast(Iterator[Any], iter(df.itertuples()))


def chunked(items: Iterable[T], size: int) -> Iterator[list[T]]:
    batch: list[T] = []
    for it in items:
        batch.append(it)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def to_multipolygon(geom: BaseGeometry | None) -> MultiPolygon | None:
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "Polygon":
        return MultiPolygon([geom])
    if geom.geom_type == "MultiPolygon":
        return geom
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        flat = []
        for p in polys:
            flat.extend(p.geoms if p.geom_type == "MultiPolygon" else [p])
        return MultiPolygon(flat) if flat else None
    return None


def to_multilinestring(geom: BaseGeometry | None) -> MultiLineString | None:
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "LineString":
        return MultiLineString([geom])
    if geom.geom_type == "MultiLineString":
        return geom
    return None


def clean_polygons(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, int]:
    """Fix invalid geometries (buffer(0)/make_valid), coerce to MultiPolygon, drop empties.
    Returns the cleaned frame and the number of rows dropped."""
    gdf = ensure_crs(gdf, GEOGRAPHIC_CRS)
    before = len(gdf)
    geoms = gdf.geometry
    invalid = ~geoms.is_valid
    if invalid.any():
        gdf.loc[invalid, "geometry"] = geoms[invalid].make_valid()
    gdf["geometry"] = gdf.geometry.apply(to_multipolygon)
    gdf = gdf[gdf.geometry.notna()]
    gdf = gdf.set_geometry("geometry", crs=GEOGRAPHIC_CRS)
    return gdf, before - len(gdf)


def write_parquet(df: pd.DataFrame | gpd.GeoDataFrame, name: str, subdir: str = "") -> Path:
    settings = get_settings()
    out_dir = settings.processed_dir / subdir if subdir else settings.processed_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.parquet"
    if isinstance(df, gpd.GeoDataFrame):
        df.to_parquet(path, index=False)
    else:
        df.to_parquet(path, index=False, engine="pyarrow")
    log.info("parquet_written", path=str(path), rows=len(df))
    return path


def read_parquet(name: str, subdir: str = "") -> pd.DataFrame | gpd.GeoDataFrame | None:
    settings = get_settings()
    path = (
        settings.processed_dir / subdir if subdir else settings.processed_dir
    ) / f"{name}.parquet"
    if not path.exists():
        return None
    try:
        return gpd.read_parquet(path)
    except Exception:
        return pd.read_parquet(path)


def gdf_to_postgis(
    gdf: gpd.GeoDataFrame, table: str, session: Session, *, if_exists: str = "append"
) -> int:
    """Append a GeoDataFrame to an existing PostGIS table via the session's connection."""
    if len(gdf) == 0:
        return 0
    gdf = ensure_crs(gdf, GEOGRAPHIC_CRS)
    conn = session.connection()
    gdf.to_postgis(table, conn, if_exists=if_exists, index=False, chunksize=2000)
    return len(gdf)


def bulk_insert(
    session: Session, model: type, rows: list[dict[str, Any]], batch: int = 2000
) -> int:
    n = 0
    for chunk in chunked(rows, batch):
        session.bulk_insert_mappings(model, chunk)
        n += len(chunk)
    session.flush()
    return n

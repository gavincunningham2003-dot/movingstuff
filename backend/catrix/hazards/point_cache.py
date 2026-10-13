"""Disk cache for point web-service lookups (USGS design maps, CGS rasters, NOAA Atlas 14).

Keyed by (source_id, lon, lat) rounded to 4 decimals (~11 m), persisted as Parquet in
``data/cache/points/<source_id>.parquet`` so re-runs and portfolio uploads do not
re-query the same coordinates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from catrix.config import get_settings


def _key(lon: float, lat: float) -> str:
    return f"{round(lon, 4):.4f},{round(lat, 4):.4f}"


class PointCache:
    def __init__(self, source_id: str):
        self.source_id = source_id
        self.path: Path = get_settings().cache_dir / "points" / f"{source_id}.parquet"
        self._data: dict[str, Any] = {}
        self._dirty = False
        if self.path.exists():
            df = pd.read_parquet(self.path)
            self._data = {k: json.loads(v) for k, v in zip(df["key"], df["payload"], strict=True)}

    def get(self, lon: float, lat: float) -> Any | None:
        return self._data.get(_key(lon, lat))

    def put(self, lon: float, lat: float, payload: Any) -> None:
        self._data[_key(lon, lat)] = payload
        self._dirty = True

    def flush(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(
            {
                "key": list(self._data.keys()),
                "payload": [json.dumps(v) for v in self._data.values()],
            }
        )
        df.to_parquet(self.path, index=False)
        self._dirty = False

    def __len__(self) -> int:
        return len(self._data)

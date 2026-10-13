"""Shared fixtures. Synthetic fixtures are explicitly labelled as such and never used
outside tests."""

from __future__ import annotations

import os
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point data directories at a temp folder and the registry at the repo copy."""
    monkeypatch.setenv("CATRIX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CATRIX_HTTP_MAX_RETRIES", "1")
    monkeypatch.setenv("CATRIX_ENV", "test")
    from catrix.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    s.ensure_dirs()
    # registry lives next to data_dir by default; copy the real one so tests use real specs
    repo_registry = Path(__file__).resolve().parents[2] / "data_sources.yaml"
    (tmp_path / "data_sources.yaml").write_text(
        repo_registry.read_text(encoding="utf-8"), encoding="utf-8"
    )
    from catrix.ingest.registry import load_registry

    load_registry.cache_clear()
    yield
    get_settings.cache_clear()
    load_registry.cache_clear()


@pytest.fixture
def synthetic_points() -> pd.DataFrame:
    """SYNTHETIC test points inside California (Bay Area / Sierra foothills)."""
    return pd.DataFrame(
        {"lon": [-122.27, -120.65, -118.25, -121.0], "lat": [37.87, 38.75, 34.05, 39.0]}
    )


@pytest.fixture
def synthetic_polygons() -> gpd.GeoDataFrame:
    """SYNTHETIC hazard polygons: one around Berkeley (Very High), one around Placerville (High)."""
    return gpd.GeoDataFrame(
        {"classification": ["Very High", "High"], "class_code": [3, 2]},
        geometry=[
            Polygon([(-122.4, 37.8), (-122.1, 37.8), (-122.1, 38.0), (-122.4, 38.0)]),
            Polygon([(-120.8, 38.6), (-120.5, 38.6), (-120.5, 38.9), (-120.8, 38.9)]),
        ],
        crs="EPSG:4326",
    )


def integration_db_url() -> str | None:
    return os.environ.get("CATRIX_TEST_DATABASE_URL")


requires_db = pytest.mark.skipif(
    integration_db_url() is None,
    reason="CATRIX_TEST_DATABASE_URL not set (PostGIS integration tests)",
)

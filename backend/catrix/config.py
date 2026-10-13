"""Typed, environment-driven configuration.

All settings are read from environment variables prefixed with ``CATRIX_`` (or a ``.env``
file in the working directory). Nothing here contains secrets; see ``.env.example``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# California bounding box in WGS84 (generous; used for coarse validation only).
CALIFORNIA_BBOX: tuple[float, float, float, float] = (-124.6, 32.5, -114.1, 42.1)

# CRS used for metre-based distance/area work. NAD83 / California Albers (EPSG:3310) is an
# equal-area projection covering the whole state with low distortion.
PROJECTED_CRS = "EPSG:3310"
GEOGRAPHIC_CRS = "EPSG:4326"
WEB_MERCATOR = "EPSG:3857"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CATRIX_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = False

    database_url: str = "postgresql+psycopg://catrix:catrix@localhost:5432/catrix"
    test_database_url: str | None = None

    data_dir: Path = Field(default=Path("./data"))
    random_seed: int = 42

    http_timeout_seconds: float = 60.0
    http_max_retries: int = 4
    http_user_agent: str = "catrix/0.1 (+https://github.com/example/catrix)"

    openaddresses_token: str | None = None
    address_sample_size: int = 15000
    batch_size: int = 500

    mlflow_tracking_uri: str | None = None

    # API guard rails
    max_page_size: int = 500
    max_bbox_area_deg2: float = 4.0  # ~2x2 degrees; prevents unbounded spatial queries

    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand(cls, v: str | Path) -> Path:
        return Path(v).expanduser()

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def registry_path(self) -> Path:
        # data_sources.yaml lives at the repository root, next to the data directory.
        return self.data_dir.parent / "data_sources.yaml"

    def ensure_dirs(self) -> None:
        for d in (self.raw_dir, self.processed_dir, self.cache_dir, self.models_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

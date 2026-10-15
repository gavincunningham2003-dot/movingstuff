"""SQLAlchemy ORM models for the CATRIX PostGIS schema.

Geometry columns use GeoAlchemy2 with SRID 4326 (storage) — metre-based analysis is done in
EPSG:3310 in the application layer or via ``ST_Transform`` in queries. Large raster assets
are kept on disk (``data/raw``), never inside the database.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


def _now() -> datetime:
    return datetime.utcnow()


# --------------------------------------------------------------------------- regions / sources
class Region(Base):
    """Geographic regions (currently California counties from TIGER/Line)."""

    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(primary_key=True)
    region_type: Mapped[str] = mapped_column(String(32), index=True)  # 'county'
    fips: Mapped[str] = mapped_column(String(5), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    name_lsad: Mapped[str | None] = mapped_column(String(160))
    land_area_m2: Mapped[float | None] = mapped_column(Float)
    geom = mapped_column(Geometry(geometry_type="MULTIPOLYGON", srid=4326), nullable=False)

    __table_args__ = (Index("ix_regions_geom", "geom", postgresql_using="gist"),)


class HazardSource(Base):
    """Ingestion state for each dataset in ``data_sources.yaml``."""

    __tablename__ = "hazard_sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # registry id
    name: Mapped[str] = mapped_column(String(255))
    provider: Mapped[str | None] = mapped_column(String(255))
    version: Mapped[str | None] = mapped_column(String(128))
    source_url: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    record_count: Mapped[int | None] = mapped_column(Integer)
    ingestion_status: Mapped[str] = mapped_column(String(32), default="never_ingested")
    error: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(128))
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)


# --------------------------------------------------------------------------- locations
class Address(Base):
    """A property location (OpenAddresses point). Not one-to-one with buildings."""

    __tablename__ = "addresses"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("hazard_sources.id"), index=True)
    source_file: Mapped[str | None] = mapped_column(String(255))
    hash: Mapped[str | None] = mapped_column(String(64), index=True)
    number: Mapped[str | None] = mapped_column(String(32))
    street: Mapped[str | None] = mapped_column(String(255))
    unit: Mapped[str | None] = mapped_column(String(64))
    city: Mapped[str | None] = mapped_column(String(128))
    district: Mapped[str | None] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(32))
    postcode: Mapped[str | None] = mapped_column(String(16))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    county_fips: Mapped[str | None] = mapped_column(ForeignKey("regions.fips"), index=True)
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    geom = mapped_column(Geometry(geometry_type="POINT", srid=4326), nullable=False)
    dedup_key: Mapped[str | None] = mapped_column(String(512), index=True)

    building: Mapped[AddressBuilding | None] = relationship(back_populates="address", uselist=False)
    assessments: Mapped[list[PropertyHazardAssessment]] = relationship(back_populates="address")

    __table_args__ = (
        Index("ix_addresses_geom", "geom", postgresql_using="gist"),
        UniqueConstraint("source_id", "hash", name="uq_addresses_source_hash"),
    )

    @property
    def formatted(self) -> str | None:
        parts = [p for p in (self.number, self.street, self.unit) if p]
        line = " ".join(parts) if parts else None
        tail = ", ".join(p for p in (self.city, self.postcode) if p)
        if line and tail:
            return f"{line}, {tail}"
        return line or tail or None


class Building(Base):
    __tablename__ = "buildings"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("hazard_sources.id"), index=True)
    footprint_area_m2: Mapped[float] = mapped_column(Float)
    release: Mapped[str | None] = mapped_column(String(32))
    capture_dates_range: Mapped[str | None] = mapped_column(String(64))
    county_fips: Mapped[str | None] = mapped_column(String(5), index=True)
    geom = mapped_column(Geometry(geometry_type="POLYGON", srid=4326), nullable=False)

    __table_args__ = (Index("ix_buildings_geom", "geom", postgresql_using="gist"),)


class AddressBuilding(Base):
    """Address-to-footprint association with explicit matching uncertainty."""

    __tablename__ = "address_buildings"

    address_id: Mapped[int] = mapped_column(ForeignKey("addresses.id"), primary_key=True)
    building_id: Mapped[int | None] = mapped_column(ForeignKey("buildings.id"), index=True)
    match_method: Mapped[str] = mapped_column(String(32))  # 'contains' | 'nearest' | 'none'
    match_distance_m: Mapped[float | None] = mapped_column(Float)
    buildings_within_100m: Mapped[int | None] = mapped_column(Integer)
    uncertainty: Mapped[str] = mapped_column(String(32))  # 'low' | 'medium' | 'high' | 'unmatched'

    address: Mapped[Address] = relationship(back_populates="building")
    footprint: Mapped[Building | None] = relationship()


class Structure(Base):
    """USACE National Structure Inventory point: modelled building attributes used for
    Hazus-style vulnerability (building type, storeys, foundation, year built, value)."""

    __tablename__ = "structures"

    id: Mapped[int] = mapped_column(primary_key=True)  # NSI fd_id
    source_id: Mapped[str] = mapped_column(ForeignKey("hazard_sources.id"), index=True)
    bid: Mapped[str | None] = mapped_column(String(64))
    bldgtype: Mapped[str | None] = mapped_column(String(4))  # W M S C H
    occtype: Mapped[str | None] = mapped_column(String(16))  # e.g. RES1-1SNB
    damage_category: Mapped[str | None] = mapped_column(String(8))  # RES COM IND PUB
    num_story: Mapped[int | None] = mapped_column(Integer)
    sqft: Mapped[float | None] = mapped_column(Float)
    found_type: Mapped[str | None] = mapped_column(String(4))  # S C B P I W F
    found_ht_ft: Mapped[float | None] = mapped_column(Float)
    ground_elev_ft: Mapped[float | None] = mapped_column(Float)
    year_built: Mapped[int | None] = mapped_column(Integer)
    val_struct_usd: Mapped[float | None] = mapped_column(Float)
    val_cont_usd: Mapped[float | None] = mapped_column(Float)
    firmzone: Mapped[str | None] = mapped_column(String(8))
    static_bfe_ft: Mapped[float | None] = mapped_column(Float)
    county_fips: Mapped[str | None] = mapped_column(String(5), index=True)
    geom = mapped_column(Geometry(geometry_type="POINT", srid=4326), nullable=False)

    __table_args__ = (Index("ix_structures_geom", "geom", postgresql_using="gist"),)


class AddressStructure(Base):
    """Address-to-NSI-structure association with matching uncertainty."""

    __tablename__ = "address_structures"

    address_id: Mapped[int] = mapped_column(ForeignKey("addresses.id"), primary_key=True)
    structure_id: Mapped[int | None] = mapped_column(ForeignKey("structures.id"), index=True)
    match_distance_m: Mapped[float | None] = mapped_column(Float)
    uncertainty: Mapped[str] = mapped_column(String(32))  # low | medium | high | unmatched

    structure: Mapped[Structure | None] = relationship()


# --------------------------------------------------------------------------- hazards
class WildfireEvent(Base):
    """CAL FIRE FRAP perimeter."""

    __tablename__ = "wildfire_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("hazard_sources.id"), index=True)
    source_object_id: Mapped[int] = mapped_column(Integer)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    fire_name: Mapped[str | None] = mapped_column(String(255))
    agency: Mapped[str | None] = mapped_column(String(16))
    unit_id: Mapped[str | None] = mapped_column(String(16))
    inc_num: Mapped[str | None] = mapped_column(String(32))
    irwin_id: Mapped[str | None] = mapped_column(String(64))
    alarm_date: Mapped[datetime | None] = mapped_column(Date)
    cont_date: Mapped[datetime | None] = mapped_column(Date)
    cause: Mapped[int | None] = mapped_column(Integer)
    gis_acres: Mapped[float | None] = mapped_column(Float)
    objective: Mapped[int | None] = mapped_column(Integer)
    complex_name: Mapped[str | None] = mapped_column(String(255))
    geom = mapped_column(Geometry(geometry_type="MULTIPOLYGON", srid=4326), nullable=False)

    __table_args__ = (
        Index("ix_wildfire_events_geom", "geom", postgresql_using="gist"),
        UniqueConstraint("source_id", "source_object_id", name="uq_wildfire_source_oid"),
    )


class HazardGeometry(Base):
    """Generic hazard polygons (FHSZ, NFHL flood zones, tsunami areas, liquefaction zones...)."""

    __tablename__ = "hazard_geometries"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("hazard_sources.id"), index=True)
    hazard_id: Mapped[str] = mapped_column(String(64), index=True)
    source_object_id: Mapped[str | None] = mapped_column(String(64))
    classification: Mapped[str | None] = mapped_column(String(64), index=True)
    class_code: Mapped[int | None] = mapped_column(Integer)
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    geom = mapped_column(Geometry(geometry_type="MULTIPOLYGON", srid=4326), nullable=False)

    __table_args__ = (
        Index("ix_hazard_geometries_geom", "geom", postgresql_using="gist"),
        Index("ix_hazard_geometries_hazard_class", "hazard_id", "classification"),
    )


class HazardLine(Base):
    """Linear hazard features (faults, NHD flowlines)."""

    __tablename__ = "hazard_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("hazard_sources.id"), index=True)
    hazard_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    geom = mapped_column(Geometry(geometry_type="MULTILINESTRING", srid=4326), nullable=False)

    __table_args__ = (Index("ix_hazard_lines_geom", "geom", postgresql_using="gist"),)


class EarthquakeEvent(Base):
    __tablename__ = "earthquake_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # USGS event id
    source_id: Mapped[str] = mapped_column(ForeignKey("hazard_sources.id"), index=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    magnitude: Mapped[float] = mapped_column(Float, index=True)
    depth_km: Mapped[float | None] = mapped_column(Float)
    place: Mapped[str | None] = mapped_column(String(255))
    geom = mapped_column(Geometry(geometry_type="POINT", srid=4326), nullable=False)

    __table_args__ = (Index("ix_earthquake_events_geom", "geom", postgresql_using="gist"),)


class RegionalIndex(Base):
    """County-level benchmark values (FEMA NRI). Context only."""

    __tablename__ = "regional_indices"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("hazard_sources.id"), index=True)
    county_fips: Mapped[str] = mapped_column(String(5), index=True)
    metric: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[float | None] = mapped_column(Float)
    rating: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("source_id", "county_fips", "metric", name="uq_regional_metric"),
    )


# --------------------------------------------------------------------------- observations
class EnvironmentalObservation(Base):
    """Per-address environmental features (elevation, slope, land cover, distances...)."""

    __tablename__ = "environmental_observations"

    address_id: Mapped[int] = mapped_column(ForeignKey("addresses.id"), primary_key=True)
    elevation_m: Mapped[float | None] = mapped_column(Float)
    slope_deg: Mapped[float | None] = mapped_column(Float)
    aspect_deg: Mapped[float | None] = mapped_column(Float)
    land_cover_code: Mapped[int | None] = mapped_column(Integer)
    land_cover_class: Mapped[str | None] = mapped_column(String(64))
    fuel_model: Mapped[str | None] = mapped_column(String(64))
    distance_to_fault_m: Mapped[float | None] = mapped_column(Float)
    distance_to_watercourse_m: Mapped[float | None] = mapped_column(Float)
    fires_within_5km_since_1990: Mapped[int | None] = mapped_column(Integer)
    years_since_last_fire_within_1km: Mapped[float | None] = mapped_column(Float)
    sources: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PropertyHazardAssessment(Base):
    """Common output contract for every hazard module, persisted per address."""

    __tablename__ = "property_hazard_assessments"

    id: Mapped[int] = mapped_column(primary_key=True)
    address_id: Mapped[int] = mapped_column(ForeignKey("addresses.id"), index=True)
    hazard_id: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("hazard_sources.id"))
    source_version: Mapped[str | None] = mapped_column(String(128))
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    classification: Mapped[str | None] = mapped_column(String(128))
    value: Mapped[float | None] = mapped_column(Float)
    units: Mapped[str | None] = mapped_column(String(128))
    probability_horizon: Mapped[str | None] = mapped_column(String(64))
    coverage_status: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[str | None] = mapped_column(String(16))
    method: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    caveats: Mapped[list[Any] | None] = mapped_column(JSON)
    # The refresh that wrote this row (None for rows written before versioning).
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment_runs.id", ondelete="SET NULL"), index=True
    )

    address: Mapped[Address] = relationship(back_populates="assessments")

    __table_args__ = (
        UniqueConstraint("address_id", "hazard_id", name="uq_assessment_addr_hazard"),
    )


class AssessmentRun(Base):
    """One versioned refresh of the assessment table (hazards or the composite score)."""

    __tablename__ = "assessment_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    kind: Mapped[str] = mapped_column(String(32), default="hazards")
    hazard_ids: Mapped[list[Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    n_addresses: Mapped[int] = mapped_column(Integer, default=0)
    counts: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class AssessmentStaging(Base):
    """Rows of an in-progress refresh; swapped into the live table on commit."""

    __tablename__ = "assessment_staging"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment_runs.id", ondelete="CASCADE"), index=True
    )
    address_id: Mapped[int] = mapped_column(Integer)
    hazard_id: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str] = mapped_column(String(64))
    source_version: Mapped[str | None] = mapped_column(String(128))
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    classification: Mapped[str | None] = mapped_column(String(128))
    value: Mapped[float | None] = mapped_column(Float)
    units: Mapped[str | None] = mapped_column(String(128))
    probability_horizon: Mapped[str | None] = mapped_column(String(64))
    coverage_status: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[str | None] = mapped_column(String(16))
    method: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    caveats: Mapped[list[Any] | None] = mapped_column(JSON)


# --------------------------------------------------------------------------- ML
class MLFeatureRow(Base):
    """Location-year feature/label rows for the wildfire model."""

    __tablename__ = "ml_features"

    id: Mapped[int] = mapped_column(primary_key=True)
    address_id: Mapped[int] = mapped_column(ForeignKey("addresses.id"), index=True)
    feature_year: Mapped[int] = mapped_column(Integer, index=True)  # year T (features through end)
    target_year: Mapped[int] = mapped_column(Integer, index=True)  # year T+1
    label: Mapped[int | None] = mapped_column(Integer)  # 1 = inside a mapped perimeter in T+1
    label_quality: Mapped[str | None] = mapped_column(String(32))
    features: Mapped[dict[str, Any]] = mapped_column(JSON)
    spatial_block: Mapped[str | None] = mapped_column(String(32), index=True)
    dataset_version: Mapped[str] = mapped_column(String(64), index=True)

    __table_args__ = (
        UniqueConstraint(
            "address_id", "feature_year", "dataset_version", name="uq_mlfeat_addr_year"
        ),
    )


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(64), unique=True)
    algorithm: Mapped[str] = mapped_column(String(64))
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    dataset_version: Mapped[str] = mapped_column(String(64))
    target_definition: Mapped[str] = mapped_column(Text)
    train_period: Mapped[str] = mapped_column(String(64))
    validation_period: Mapped[str] = mapped_column(String(64))
    test_period: Mapped[str] = mapped_column(String(64))
    spatial_holdout: Mapped[str | None] = mapped_column(String(128))
    artifact_path: Mapped[str | None] = mapped_column(String(512))
    feature_list: Mapped[list[Any]] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    address_id: Mapped[int] = mapped_column(ForeignKey("addresses.id"), index=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"), index=True)
    prediction_year: Mapped[int] = mapped_column(Integer)
    probability: Mapped[float] = mapped_column(Float)
    base_value: Mapped[float | None] = mapped_column(Float)
    features: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    shap_values: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint("address_id", "model_version_id", name="uq_prediction_addr_model"),
    )


# --------------------------------------------------------------------------- portfolios
class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    n_rows: Mapped[int] = mapped_column(Integer, default=0)
    n_valid: Mapped[int] = mapped_column(Integer, default=0)
    n_invalid: Mapped[int] = mapped_column(Integer, default=0)
    n_duplicates: Mapped[int] = mapped_column(Integer, default=0)  # exact duplicates
    n_shared_location: Mapped[int] = mapped_column(Integer, default=0)
    n_locations: Mapped[int] = mapped_column(Integer, default=0)
    validation_errors: Mapped[list[Any] | None] = mapped_column(JSON)
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    properties: Mapped[list[PortfolioProperty]] = relationship(back_populates="portfolio")


class PortfolioProperty(Base):
    __tablename__ = "portfolio_properties"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("portfolios.id"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    property_id: Mapped[str] = mapped_column(String(128), index=True)
    policy_id: Mapped[str | None] = mapped_column(String(128))
    address_text: Mapped[str | None] = mapped_column(String(512))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    property_value: Mapped[float | None] = mapped_column(Float)  # building replacement value
    contents_value: Mapped[float | None] = mapped_column(Float)
    insured_value: Mapped[float | None] = mapped_column(Float)  # sum insured / TIV
    policy_limit: Mapped[float | None] = mapped_column(Float)
    deductible: Mapped[float | None] = mapped_column(Float)
    county_fips: Mapped[str | None] = mapped_column(String(5), index=True)
    is_duplicate_of: Mapped[int | None] = mapped_column(Integer)
    duplicate_kind: Mapped[str | None] = mapped_column(
        String(24)
    )  # exact|shared_location|repeated_id
    location_key: Mapped[str | None] = mapped_column(String(64), index=True)
    hazards: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    composite_score: Mapped[float | None] = mapped_column(Float)
    financial: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    geom = mapped_column(Geometry(geometry_type="POINT", srid=4326), nullable=False)

    portfolio: Mapped[Portfolio] = relationship(back_populates="properties")

    __table_args__ = (Index("ix_portfolio_properties_geom", "geom", postgresql_using="gist"),)


class FinancialScenarioAssumption(Base):
    """Versioned damage-ratio and vulnerability assumptions used by the finance module."""

    __tablename__ = "financial_scenario_assumptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String(64), index=True)
    hazard_id: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(Text)
    damage_ratio: Mapped[float] = mapped_column(Float)
    basis: Mapped[str] = mapped_column(Text)  # citation / rationale
    kind: Mapped[str] = mapped_column(String(32))  # 'externally_derived' | 'assumed'
    version: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# --------------------------------------------------------------------------- jobs / quality
class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Leases and retries (see catrix.portfolio.jobs)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_seconds: Mapped[int] = mapped_column(Integer, default=600)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)


class DataQualityReport(Base):
    __tablename__ = "data_quality_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    stage: Mapped[str] = mapped_column(String(64), index=True)
    check_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    n_checked: Mapped[int] = mapped_column(Integer, default=0)
    n_failed: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)

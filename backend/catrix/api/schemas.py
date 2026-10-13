"""Pydantic response/request models for the versioned API (``/api/v1``).

These models are the contract shared with the React frontend. Field names are stable;
additive changes only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

CoverageStatus = Literal["covered", "not_in_hazard_area", "outside_coverage", "unavailable"]
Confidence = Literal["high", "medium", "low"]


# --------------------------------------------------------------------------- health / meta
class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ok", "unavailable"]
    timestamp: datetime
    detail: str | None = None


class DataSourceOut(BaseModel):
    id: str
    name: str
    provider: str
    category: str
    url: str | None = None
    api_url: str | None = None
    format: str | None = None
    crs: str | None = None
    geographic_coverage: str | None = None
    temporal_coverage: str | None = None
    licence: str | None = None
    auth_required: bool = False
    verified_on: str | None = None
    verification_status: Literal["verified", "unverified", "local_only"]
    notes: str | None = None
    # Filled from the database when an ingestion has run:
    last_ingested_at: datetime | None = None
    record_count: int | None = None
    version: str | None = None
    ingestion_status: Literal["ingested", "never_ingested", "failed", "unavailable"] = (
        "never_ingested"
    )


# --------------------------------------------------------------------------- hazards
class HazardAssessmentOut(BaseModel):
    hazard_id: str
    hazard_name: str
    source_id: str
    source_version: str | None = None
    assessed_at: datetime | None = None
    classification: str | None = Field(
        default=None, description="Categorical class as published by the source (e.g. 'Very High')."
    )
    value: float | None = Field(default=None, description="Quantitative exposure indicator.")
    units: str | None = None
    probability_horizon: str | None = Field(
        default=None,
        description="Explicit exceedance probability / horizon when the value is probabilistic "
        "(e.g. '2% in 50 years'). Null for static overlays.",
    )
    coverage_status: CoverageStatus
    confidence: Confidence | None = None
    method: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)


class HazardCatalogueOut(BaseModel):
    hazard_id: str
    name: str
    description: str
    source_ids: list[str]
    method: Literal[
        "static_overlay", "raster_sample", "point_service", "ml_model", "regional_index"
    ]
    units: str | None = None
    probability_horizon: str | None = None
    scale_note: str
    map_layer_available: bool = False


class HazardLayerFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[dict[str, Any]]
    hazard_id: str
    source_id: str
    legend: list[dict[str, Any]]
    truncated: bool = False


# --------------------------------------------------------------------------- properties
class BuildingMatchOut(BaseModel):
    building_id: int | None = None
    footprint_area_m2: float | None = None
    match_method: str | None = None
    match_distance_m: float | None = None
    buildings_within_100m: int | None = None
    uncertainty: str | None = None


class EnvironmentOut(BaseModel):
    elevation_m: float | None = None
    slope_deg: float | None = None
    aspect_deg: float | None = None
    land_cover_class: str | None = None
    fuel_model: str | None = None
    distance_to_fault_m: float | None = None
    distance_to_watercourse_m: float | None = None
    fires_within_5km_since_1990: int | None = None
    years_since_last_fire_within_1km: float | None = None
    sources: dict[str, str] = Field(default_factory=dict)


class CompositeScoreOut(BaseModel):
    score: float | None = Field(
        default=None, description="0-100 screening index. NOT a probability or premium."
    )
    label: str
    contributions: list[dict[str, Any]]
    missing_hazards: list[str]
    weights_note: str
    method_version: str


class WildfirePredictionOut(BaseModel):
    model_version: str
    target_definition: str
    probability: float | None
    prediction_year: int | None = None
    threshold: float | None = None
    predicted_class: bool | None = None
    base_value: float | None = None
    features: dict[str, float | None] = Field(default_factory=dict)
    shap: list[dict[str, Any]] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class PropertySummaryOut(BaseModel):
    id: int
    external_id: str | None = None
    address: str | None = None
    city: str | None = None
    postcode: str | None = None
    county: str | None = None
    county_fips: str | None = None
    lat: float
    lon: float
    source_id: str
    composite_score: float | None = None
    wildfire_fhsz: str | None = None
    flood_zone: str | None = None
    pga_2pc50_g: float | None = None


class PropertyDetailOut(PropertySummaryOut):
    acquired_at: datetime | None = None
    building: BuildingMatchOut | None = None
    environment: EnvironmentOut | None = None
    hazards: list[HazardAssessmentOut] = Field(default_factory=list)
    prediction: WildfirePredictionOut | None = None
    score: CompositeScoreOut | None = None
    coverage: dict[str, CoverageStatus] = Field(default_factory=dict)
    financial: FinancialScenarioSetOut | None = None


class PagedProperties(BaseModel):
    items: list[PropertySummaryOut]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------- finance
class FinancialAssumptionOut(BaseModel):
    name: str
    value: float | str
    kind: Literal["observed", "externally_derived", "assumed"]
    source: str | None = None


class FinancialScenarioOut(BaseModel):
    scenario_id: str
    hazard_id: str
    description: str
    damage_ratio: float
    exposed_value: float | None
    gross_loss: float | None
    deductible_applied: float | None = None
    limit_applied: float | None = None
    net_insured_loss: float | None = None
    assumptions: list[FinancialAssumptionOut]
    caveats: list[str]


class FinancialScenarioSetOut(BaseModel):
    exposed_value: float | None
    exposed_value_kind: Literal["observed", "externally_derived", "assumed", "missing"]
    scenarios: list[FinancialScenarioOut]
    expected_annual_loss_available: bool = False
    note: str


# --------------------------------------------------------------------------- portfolio
class PortfolioUploadResponse(BaseModel):
    portfolio_id: str
    job_id: str
    status: str
    n_rows_received: int


class PortfolioStatusOut(BaseModel):
    portfolio_id: str
    name: str | None
    status: Literal["pending", "running", "completed", "failed"]
    created_at: datetime
    n_rows: int
    n_valid: int
    n_invalid: int
    n_duplicates: int
    validation_errors: list[dict[str, Any]]
    progress: float = 0.0
    error: str | None = None


class CountySummaryOut(BaseModel):
    county: str | None
    county_fips: str | None
    n_properties: int
    share_pct: float
    total_insured_value: float | None
    mean_composite_score: float | None
    n_fhsz_very_high: int
    n_sfha: int
    mean_pga_2pc50_g: float | None


class ConcentrationCellOut(BaseModel):
    cell_id: str
    centroid_lat: float
    centroid_lon: float
    n_properties: int
    total_insured_value: float | None
    share_of_tiv_pct: float | None
    dominant_hazard: str | None


class TopExposureOut(BaseModel):
    property_id: str
    lat: float
    lon: float
    county: str | None
    insured_value: float | None
    composite_score: float | None
    drivers: list[str]


class PortfolioAnalysisOut(BaseModel):
    portfolio_id: str
    generated_at: datetime
    summary: dict[str, Any]
    hazard_distribution: dict[str, dict[str, int]]
    coverage: dict[str, dict[str, int]]
    by_county: list[CountySummaryOut]
    concentration: list[ConcentrationCellOut]
    top_exposures: list[TopExposureOut]
    financial: dict[str, Any] | None
    warnings: list[str]


class PortfolioSummaryOut(BaseModel):
    """Summary over the base (ingested) property dataset — the 'Executive Overview'."""

    total_properties: int
    counties: list[CountySummaryOut]
    hazard_distribution: dict[str, dict[str, int]]
    coverage: dict[str, dict[str, int]]
    exposure_value: dict[str, Any]
    model: dict[str, Any] | None
    interpretation_notes: list[str]
    generated_at: datetime


# --------------------------------------------------------------------------- ML
class ModelMetricsOut(BaseModel):
    name: str
    version: str
    algorithm: str
    trained_at: datetime | None
    target_definition: str
    train_period: str
    validation_period: str
    test_period: str
    spatial_holdout: str | None
    n_train: int
    n_test: int
    prevalence_train: float
    prevalence_test: float
    metrics: dict[str, float | None]
    threshold: float
    confusion: dict[str, int]
    roc_curve: list[dict[str, float]]
    pr_curve: list[dict[str, float]]
    calibration: list[dict[str, float]]
    feature_importance: list[dict[str, Any]]
    shap_summary: list[dict[str, Any]]
    geographic_performance: list[dict[str, Any]]
    is_selected: bool


class ModelsMetricsResponse(BaseModel):
    models: list[ModelMetricsOut]
    selected_model: str | None
    feature_list: list[str]
    limitations: list[str]
    status: Literal["available", "not_trained", "insufficient_data"]
    status_detail: str | None = None


# --------------------------------------------------------------------------- data quality
class DatasetQualityOut(BaseModel):
    source_id: str
    name: str
    verification_status: str
    ingestion_status: str
    last_ingested_at: datetime | None
    record_count: int | None
    version: str | None
    coverage_pct: float | None
    missing_rate_pct: float | None
    errors: list[str]


class QualityCheckOut(BaseModel):
    name: str
    status: Literal["pass", "warn", "fail", "skipped"]
    n_checked: int
    n_failed: int
    detail: str | None


class DataQualityOut(BaseModel):
    generated_at: datetime
    datasets: list[DatasetQualityOut]
    checks: list[QualityCheckOut]
    processing_errors: list[dict[str, Any]]
    unavailable_sources: list[str]
    model_version: str | None
    note: str


class JobOut(BaseModel):
    job_id: str
    job_type: str
    status: str
    progress: float
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    result: dict[str, Any] | None


PropertyDetailOut.model_rebuild()

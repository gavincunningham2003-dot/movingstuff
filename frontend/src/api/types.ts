/**
 * TypeScript mirror of backend/catrix/api/schemas.py (the /api/v1 contract).
 * Field names are snake_case exactly as the Pydantic models declare them.
 * `datetime` fields arrive as ISO-8601 strings.
 */

import type { Feature, FeatureCollection, Point } from 'geojson';

export type CoverageStatus = 'covered' | 'not_in_hazard_area' | 'outside_coverage' | 'unavailable';
export type Confidence = 'high' | 'medium' | 'low';

// --------------------------------------------------------------------------- health / meta
export interface HealthResponse {
  status: 'ok' | 'degraded';
  version: string;
  database: 'ok' | 'unavailable';
  timestamp: string;
  detail?: string | null;
}

export type VerificationStatus = 'verified' | 'unverified' | 'local_only';
export type IngestionStatus = 'ingested' | 'never_ingested' | 'failed' | 'unavailable' | 'planned' | 'superseded';

export interface DataSourceOut {
  planned?: boolean;
  superseded_by?: string | null;
  id: string;
  name: string;
  provider: string;
  category: string;
  url?: string | null;
  api_url?: string | null;
  format?: string | null;
  crs?: string | null;
  geographic_coverage?: string | null;
  temporal_coverage?: string | null;
  licence?: string | null;
  auth_required: boolean;
  verified_on?: string | null;
  verification_status: VerificationStatus;
  notes?: string | null;
  last_ingested_at?: string | null;
  record_count?: number | null;
  version?: string | null;
  ingestion_status: IngestionStatus;
}

// --------------------------------------------------------------------------- hazards
export interface HazardAssessmentOut {
  hazard_id: string;
  hazard_name: string;
  source_id: string;
  source_version?: string | null;
  assessed_at?: string | null;
  classification?: string | null;
  value?: number | null;
  units?: string | null;
  probability_horizon?: string | null;
  coverage_status: CoverageStatus;
  confidence?: Confidence | null;
  method?: string | null;
  details: Record<string, unknown>;
  caveats: string[];
}

export type HazardMethod =
  | 'static_overlay'
  | 'raster_sample'
  | 'point_service'
  | 'ml_model'
  | 'regional_index';

export interface HazardCatalogueOut {
  hazard_id: string;
  name: string;
  description: string;
  source_ids: string[];
  method: HazardMethod;
  units?: string | null;
  probability_horizon?: string | null;
  scale_note: string;
  map_layer_available: boolean;
}

/** One entry of HazardLayerFeatureCollection.legend (list[dict]) as the frontend expects it. */
export interface LegendEntry {
  label: string;
  color: string;
  value?: string | number | null;
}

export interface HazardLayerFeatureCollection {
  type: 'FeatureCollection';
  features: Feature[];
  hazard_id: string;
  source_id: string;
  legend: LegendEntry[];
  truncated: boolean;
}

// --------------------------------------------------------------------------- properties
/** USACE National Structure Inventory attributes of the nearest structure (modelled, not surveyed). */
export interface StructureAttributesOut {
  structure_id?: number | null;
  bldgtype?: string | null;
  hazus_type?: string | null;
  occtype?: string | null;
  damage_category?: string | null;
  num_story?: number | null;
  sqft?: number | null;
  found_type?: string | null;
  found_ht_ft?: number | null;
  ground_elev_ft?: number | null;
  year_built?: number | null;
  val_struct_usd?: number | null;
  firmzone?: string | null;
  match_distance_m?: number | null;
  uncertainty?: string | null;
  source_id: string;
}

export interface BuildingMatchOut {
  building_id?: number | null;
  footprint_area_m2?: number | null;
  match_method?: string | null;
  match_distance_m?: number | null;
  buildings_within_100m?: number | null;
  uncertainty?: string | null;
  structure?: StructureAttributesOut | null;
}

export interface EnvironmentOut {
  elevation_m?: number | null;
  slope_deg?: number | null;
  aspect_deg?: number | null;
  land_cover_class?: string | null;
  fuel_model?: string | null;
  distance_to_fault_m?: number | null;
  distance_to_watercourse_m?: number | null;
  fires_within_5km_since_1990?: number | null;
  years_since_last_fire_within_1km?: number | null;
  sources: Record<string, string>;
}

/** One entry of CompositeScoreOut.contributions (list[dict]). */
export interface ScoreContribution {
  hazard_id?: string;
  hazard?: string;
  weight?: number | null;
  family_weight?: number | null;
  normalised?: number | null;
  contribution?: number | null;
  weighted_points?: number | null;
  [key: string]: unknown;
}

export interface CompositeScoreOut {
  /** 0-100 screening index. NOT a probability or premium. */
  score?: number | null;
  label: string;
  contributions: ScoreContribution[];
  missing_hazards: string[];
  weights_note: string;
  method_version: string;
}

/** One entry of WildfirePredictionOut.shap (list[dict]). */
export interface ShapEntry {
  feature: string;
  value?: number | string | null;
  contribution: number;
  [key: string]: unknown;
}

export interface WildfirePredictionOut {
  model_version: string;
  target_definition: string;
  probability: number | null;
  prediction_year?: number | null;
  threshold?: number | null;
  predicted_class?: boolean | null;
  base_value?: number | null;
  features: Record<string, number | null>;
  shap: ShapEntry[];
  caveats: string[];
}

export interface PropertySummaryOut {
  id: number;
  external_id?: string | null;
  address?: string | null;
  city?: string | null;
  postcode?: string | null;
  county?: string | null;
  county_fips?: string | null;
  lat: number;
  lon: number;
  source_id: string;
  composite_score?: number | null;
  wildfire_fhsz?: string | null;
  flood_zone?: string | null;
  pga_2pc50_g?: number | null;
}

export interface PropertyDetailOut extends PropertySummaryOut {
  acquired_at?: string | null;
  building?: BuildingMatchOut | null;
  environment?: EnvironmentOut | null;
  hazards: HazardAssessmentOut[];
  prediction?: WildfirePredictionOut | null;
  score?: CompositeScoreOut | null;
  coverage: Record<string, CoverageStatus>;
  financial?: FinancialScenarioSetOut | null;
}

export interface PointAssessmentOut {
  lon: number;
  lat: number;
  county_fips?: string | null;
  county?: string | null;
  in_california: boolean;
  hazards: HazardAssessmentOut[];
  prediction?: WildfirePredictionOut | null;
  score: CompositeScoreOut;
  assessed_at: string;
}

export interface PagedProperties {
  items: PropertySummaryOut[];
  total: number;
  limit: number;
  offset: number;
}

// --------------------------------------------------------------------------- finance
export type AssumptionKind = 'observed' | 'externally_derived' | 'assumed';

export interface FinancialAssumptionOut {
  name: string;
  value: number | string;
  kind: AssumptionKind;
  source?: string | null;
}

export interface FinancialScenarioOut {
  scenario_id: string;
  hazard_id: string;
  description: string;
  damage_ratio: number;
  exposed_value: number | null;
  gross_loss: number | null;
  deductible_applied?: number | null;
  limit_applied?: number | null;
  net_insured_loss?: number | null;
  assumptions: FinancialAssumptionOut[];
  caveats: string[];
}

export interface FinancialScenarioSetOut {
  exposed_value: number | null;
  exposed_value_kind: AssumptionKind | 'missing';
  scenarios: FinancialScenarioOut[];
  expected_annual_loss_available: boolean;
  /** Present when NSI building attributes drove Hazus damage functions. */
  building_profile?: Record<string, unknown> | null;
  note: string;
}

// --------------------------------------------------------------------------- portfolio
export interface PortfolioUploadResponse {
  portfolio_id: string;
  job_id: string;
  status: string;
  n_rows_received: number;
}

export type PortfolioStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface PortfolioStatusOut {
  portfolio_id: string;
  name: string | null;
  status: PortfolioStatus;
  created_at: string;
  n_rows: number;
  n_valid: number;
  n_invalid: number;
  n_duplicates: number;
  validation_errors: Record<string, unknown>[];
  progress: number;
  error?: string | null;
}

export interface CountySummaryOut {
  county: string | null;
  county_fips: string | null;
  n_properties: number;
  share_pct: number;
  total_insured_value: number | null;
  mean_composite_score: number | null;
  n_fhsz_very_high: number;
  n_sfha: number;
  mean_pga_2pc50_g: number | null;
}

export interface ConcentrationCellOut {
  cell_id: string;
  centroid_lat: number;
  centroid_lon: number;
  n_properties: number;
  total_insured_value: number | null;
  share_of_tiv_pct: number | null;
  dominant_hazard: string | null;
}

export interface TopExposureOut {
  property_id: string;
  lat: number;
  lon: number;
  county: string | null;
  insured_value: number | null;
  composite_score: number | null;
  drivers: string[];
}

/** hazard_id -> class label -> count */
export type HazardDistribution = Record<string, Record<string, number>>;
/** hazard_id -> coverage status -> count */
export type CoverageCounts = Record<string, Record<string, number>>;

/**
 * PortfolioAnalysisOut.financial is `dict[str, Any] | None`. The frontend
 * understands this shape (mirrors FinancialScenarioSetOut at portfolio level);
 * unknown extra keys are tolerated.
 */
export interface PortfolioFinancial {
  exposed_value?: number | null;
  exposed_value_kind?: string | null;
  scenarios?: FinancialScenarioOut[];
  note?: string | null;
  [key: string]: unknown;
}

export interface PortfolioAnalysisOut {
  portfolio_id: string;
  generated_at: string;
  summary: Record<string, unknown>;
  hazard_distribution: HazardDistribution;
  coverage: CoverageCounts;
  by_county: CountySummaryOut[];
  concentration: ConcentrationCellOut[];
  top_exposures: TopExposureOut[];
  financial: PortfolioFinancial | null;
  warnings: string[];
}

/**
 * PortfolioSummaryOut.model is `dict[str, Any] | None`. Keys the overview
 * understands; anything else is ignored.
 */
export interface OverviewModelSummary {
  status?: string;
  name?: string;
  version?: string;
  roc_auc?: number | null;
  pr_auc?: number | null;
  brier?: number | null;
  prevalence?: number | null;
  status_detail?: string | null;
  [key: string]: unknown;
}

export interface PortfolioSummaryOut {
  total_properties: number;
  counties: CountySummaryOut[];
  hazard_distribution: HazardDistribution;
  coverage: CoverageCounts;
  exposure_value: Record<string, unknown>;
  model: OverviewModelSummary | null;
  interpretation_notes: string[];
  generated_at: string;
}

// --------------------------------------------------------------------------- ML
export interface RocPoint {
  fpr: number;
  tpr: number;
  [key: string]: number;
}

export interface PrPoint {
  recall: number;
  precision: number;
  [key: string]: number;
}

/** Calibration bins: list[dict[str, float]]. Key names tolerated via lib/modelData.ts. */
export type CalibrationPoint = Record<string, number>;

export interface FeatureImportanceEntry {
  feature: string;
  importance: number;
  [key: string]: unknown;
}

export interface ShapSummaryEntry {
  feature: string;
  mean_abs_shap: number;
  [key: string]: unknown;
}

export interface GeographicPerformanceEntry {
  region: string;
  n: number;
  prevalence?: number | null;
  roc_auc?: number | null;
  pr_auc?: number | null;
  [key: string]: unknown;
}

export interface ModelMetricsOut {
  name: string;
  version: string;
  algorithm: string;
  trained_at: string | null;
  target_definition: string;
  train_period: string;
  validation_period: string;
  test_period: string;
  spatial_holdout: string | null;
  n_train: number;
  n_test: number;
  prevalence_train: number;
  prevalence_test: number;
  metrics: Record<string, number | null>;
  threshold: number;
  confusion: Record<string, number>;
  roc_curve: Record<string, number>[];
  pr_curve: Record<string, number>[];
  calibration: CalibrationPoint[];
  feature_importance: Record<string, unknown>[];
  shap_summary: Record<string, unknown>[];
  geographic_performance: Record<string, unknown>[];
  is_selected: boolean;
  operating_points?: OperatingPoint[];
  threshold_sweep?: Record<string, SweepPoint[]>;
}

export interface ConfusionAt {
  threshold: number;
  tp: number;
  fp: number;
  fn: number;
  tn: number;
  flagged_share: number;
  precision: number | null;
  recall: number | null;
  specificity: number | null;
  lift?: number | null;
  recall_ci95?: [number, number] | null;
  n_positives?: number;
  kind?: 'fixed_threshold' | 'ranking_budget';
}

export interface SweepPoint extends ConfusionAt {
  target_share: number;
}

export interface OperatingPoint {
  name: string;
  label: string;
  threshold: number;
  validation: ConfusionAt;
  test: ConfusionAt;
  spatial_test?: ConfusionAt;
}

export interface ModelVersionSummary {
  version: string;
  trained_at: string | null;
  selected_model: string | null;
  target: string;
  target_definition: string;
  dataset_version: string | null;
  excluded_features: string[];
  n_features: number;
  status: string;
  is_latest: boolean;
}

export type ModelsStatus = 'available' | 'not_trained' | 'insufficient_data';

export interface ModelsMetricsResponse {
  models: ModelMetricsOut[];
  selected_model: string | null;
  feature_list: string[];
  limitations: string[];
  status: ModelsStatus;
  status_detail?: string | null;  version?: string | null;
  target?: string | null;
  excluded_features?: string[];
  is_latest?: boolean;
}

// --------------------------------------------------------------------------- data quality
export interface DatasetQualityOut {
  source_id: string;
  name: string;
  verification_status: string;
  ingestion_status: string;
  last_ingested_at: string | null;
  record_count: number | null;
  version: string | null;
  coverage_pct: number | null;
  missing_rate_pct: number | null;
  errors: string[];
}

export type QualityCheckStatus = 'pass' | 'warn' | 'fail' | 'skipped';

export interface QualityCheckOut {
  name: string;
  status: QualityCheckStatus;
  n_checked: number;
  n_failed: number;
  detail: string | null;
}

export interface DataQualityOut {
  generated_at: string;
  datasets: DatasetQualityOut[];
  checks: QualityCheckOut[];
  processing_errors: Record<string, unknown>[];
  unavailable_sources: string[];
  model_version: string | null;
  note: string;
}

export interface JobOut {
  job_id: string;
  job_type: string;
  status: string;
  progress: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  result: Record<string, unknown> | null;
}

// --------------------------------------------------------------------------- frontend-only endpoints
// These endpoints are not in schemas.py; the backend is adding them to match.

/** GET /api/v1/properties/counties */
export interface CountyCountOut {
  county: string | null;
  county_fips: string | null;
  n: number;
}

/** Feature properties of GET /api/v1/properties/geojson */
export interface PropertyFeatureProperties {
  id: number;
  address: string | null;
  county: string | null;
  composite_score: number | null;
  wildfire_fhsz: string | null;
  flood_zone: string | null;
  pga_2pc50_g: number | null;
}

export type PropertyFeature = Feature<Point, PropertyFeatureProperties>;
export type PropertyFeatureCollection = FeatureCollection<Point, PropertyFeatureProperties>;

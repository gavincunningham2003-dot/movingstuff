# Database schema (PostGIS)

Defined in `backend/catrix/db/models.py`; migration `backend/alembic/versions/0001_initial_schema.py`.
All geometries are SRID 4326 with GiST indexes. Rasters stay on disk.

| Table | Purpose | Key columns / constraints |
|---|---|---|
| `regions` | California counties (TIGER) | `fips` unique, `geom` MULTIPOLYGON |
| `hazard_sources` | registry ingestion state | `id` = registry id, `version`, `retrieved_at`, `record_count`, `ingestion_status`, `checksum`, `error` |
| `addresses` | property locations | FK `source_id`, FK `county_fips → regions.fips`, `geom` POINT, `dedup_key`, unique (`source_id`,`hash`) |
| `buildings` | footprints | `footprint_area_m2`, `geom` POLYGON |
| `address_buildings` | address→building match | PK `address_id`, `match_method`, `match_distance_m`, `buildings_within_100m`, `uncertainty` |
| `wildfire_events` | CAL FIRE perimeters | unique (`source_id`,`source_object_id`), `year` idx, `geom` MULTIPOLYGON |
| `hazard_geometries` | generic hazard polygons (FHSZ, NFHL, tsunami, liquefaction) | `hazard_id`+`classification` idx, `class_code`, `attributes` JSON |
| `hazard_lines` | faults, flowlines | `hazard_id`, `geom` MULTILINESTRING |
| `earthquake_events` | USGS ComCat | PK USGS id, `magnitude`, `time` |
| `regional_indices` | FEMA NRI county metrics | unique (`source_id`,`county_fips`,`metric`) |
| `environmental_observations` | per-address terrain/land cover/distances | PK `address_id`, `sources` JSON |
| `property_hazard_assessments` | hazard module outputs | unique (`address_id`,`hazard_id`); `coverage_status`, `value`, `units`, `probability_horizon`, `details`, `caveats`; `_composite` pseudo-hazard row holds the screening index |
| `ml_features` | optional DB copy of location-year rows | unique (`address_id`,`feature_year`,`dataset_version`) |
| `model_versions` | trained models | `version` unique, `metrics` JSON, `is_selected` |
| `predictions` | per-address model output | unique (`address_id`,`model_version_id`), `shap_values` JSON |
| `portfolios` | uploaded portfolios | UUID PK, validation counts, `analysis` JSON, `job_id` |
| `portfolio_properties` | uploaded rows | FK `portfolio_id`, `hazards` JSON, `composite_score`, `financial` JSON, `geom` POINT |
| `financial_scenario_assumptions` | versioned damage ratios | `scenario_id`, `kind`, `version`, `active` |
| `processing_jobs` | background jobs | UUID PK, `status` idx, `progress` |
| `data_quality_reports` | check results | `stage`, `check_name`, `status`, counts, `detail` JSON |

## Spatial query patterns

- Point-in-polygon: `ST_Contains(h.geom, ST_SetSRID(ST_MakePoint(lon, lat), 4326))` with
  `DISTINCT ON (point) ORDER BY class_code DESC` to resolve overlaps.
- Distances: `ST_DWithin(geom::geography, point::geography, metres)` and
  `ST_Distance(... ::geography)`; nearest line via `ORDER BY geom <-> point LIMIT 1`.
- Map layers: `ST_SimplifyPreserveTopology(ST_Intersection(geom, envelope), tol)` with
  `tol` derived from zoom.

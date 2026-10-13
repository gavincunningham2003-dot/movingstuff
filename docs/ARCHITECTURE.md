# Architecture

CATRIX is a **modular monolith**: one Python package with clear internal boundaries, one
PostGIS database, one React frontend. No microservices, no paid APIs, no cloud dependencies.

```
                     ┌─────────────────────────────────────────────────────────┐
  public data ─────► │ ingest/   connectors (httpx cache+retry, ArcGIS paging)  │
  (ArcGIS REST,      │   ↓ validate schema, CRS → EPSG:4326, clean geometries   │
   S3 tiles, zips)   │   ↓ PostGIS tables + Parquet snapshots + hazard_sources  │
                     ├─────────────────────────────────────────────────────────┤
                     │ geo/      validation · CRS (EPSG:3310 for metres) ·      │
                     │           STRtree joins · raster sampling (Horn slope)   │
                     ├─────────────────────────────────────────────────────────┤
                     │ hazards/  15 modules → HazardResult (coverage status,    │
                     │           units, horizon, confidence, caveats)           │
                     │ scoring/  composite screening index (documented weights) │
                     │ finance/  scenario losses (assumption provenance)        │
                     │ ml/       location-year dataset (DuckDB) → models → SHAP │
                     │ portfolio/ CSV → job → enrichment → aggregation → export │
                     │ quality/  checks → data_quality_reports                  │
                     ├─────────────────────────────────────────────────────────┤
                     │ api/      FastAPI /api/v1 (OpenAPI), bounded queries     │
                     │ pipeline/ `catrix` CLI, DB-polling worker                │
                     └───────────────┬─────────────────────────────────────────┘
                                     │ JSON / GeoJSON
                     ┌───────────────▼─────────────────────────────────────────┐
                     │ frontend/  React + TS + Vite · TanStack Query · MapLibre │
                     │            Recharts · Tailwind (dark analytics theme)    │
                     └─────────────────────────────────────────────────────────┘
```

## Data flow

1. **Acquire** — `ingest.http.download_file` streams to `data/cache` with a `.meta.json`
   sidecar (URL, timestamp, sha256). ArcGIS layers are paged with `resultOffset` and
   validated against the field list recorded in `data_sources.yaml`.
2. **Validate** — coordinates (missing, impossible, 0/0, outside California), geometry
   validity (`make_valid`), schema drift (missing fields raise `SourceUnavailableError`).
3. **Standardise** — everything is stored in EPSG:4326; metre computations use EPSG:3310
   (California Albers) in Python or `geography` in PostGIS.
4. **Enrich** — hazard modules run in batches of 500 points using `VALUES`-list SQL so the
   GiST indexes do the work; point web services are cached in `data/cache/points`.
5. **Persist** — `property_hazard_assessments` (one row per address × hazard), Parquet
   snapshots of every processed table, provenance in `hazard_sources`.
6. **Serve** — API reads only; portfolio enrichment runs as a background job (FastAPI
   `BackgroundTasks` in dev, DB-polling worker in Compose).

## Hazard module contract

Every module returns a `HazardResult` with:

`hazard_id · source_id · source_version · assessed_at · classification · value · units ·
probability_horizon · coverage_status ∈ {covered, not_in_hazard_area, outside_coverage,
unavailable} · confidence · method · details · caveats`

Methods: `static_overlay` (polygon layers), `raster_sample`, `point_service`,
`regional_index` (county NRI), `ml_model`. Only `earthquake_pga` and `extreme_rainfall`
carry a true probability horizon.

## Key design decisions

- **Missing ≠ low risk.** Unavailable or unmapped hazards are excluded from the composite
  denominator and listed; a score is withheld when more than half of the weight is missing.
- **No synthetic data in the product.** Synthetic fixtures exist only under `tests/` and
  are labelled.
- **Leakage control in ML.** Features for year T are built from perimeters with
  `YEAR_ ≤ T` in one DuckDB query; present-day FHSZ is never a feature; chronological and
  spatial-block splits; tuning on validation only.
- **Bounded spatial API.** bbox area cap (4 deg²), page caps, zoom-dependent
  `ST_SimplifyPreserveTopology`, feature caps with a `truncated` flag.
- **Reproducibility.** Deterministic seeds, pinned dependency ranges, cached downloads with
  checksums, dataset/model versions recorded in the database.

## Deployment

`docker-compose.yml` runs PostGIS 16/3.4, the API (runs `alembic upgrade head` on start),
the worker, the nginx-served frontend and an optional MLflow profile. Raw data, caches and
model binaries live in the bind-mounted `data/` directory and are git-ignored.

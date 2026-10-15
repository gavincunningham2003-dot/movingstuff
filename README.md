# CATRIX — California Catastrophe Risk Intelligence Platform

CATRIX is an end-to-end, open-data catastrophe risk platform for California property
portfolios. It ingests public geographic and hazard datasets, enriches thousands of real
address points with multi-hazard exposure, trains a leakage-controlled wildfire
perimeter-exposure model, scores and aggregates uploaded insurance portfolios, and presents
everything in a dark-mode analytics dashboard with full data provenance.

It was built as a portfolio project for insurance-technology roles: the emphasis is on
analytical correctness, reproducibility and honest reporting of coverage and limitations,
not on pretending a heuristic is an actuarial model.

> **Status (2026-10-13):** complete codebase, verified data endpoints, automated tests. The
> ingestion pipeline and model training have **not** been executed in the authoring
> environment (no database was provisioned). See [PROGRESS.md](PROGRESS.md) for exactly what
> has and has not been run. No performance metrics are quoted anywhere in this repository
> because none have been produced yet.

## The business problem

A property insurer writing California risk needs, for every location in a portfolio:

| Concept | What CATRIX provides | What it is not |
|---|---|---|
| **Hazard** | Authoritative hazard layers per peril with their native units and an explicit probability horizon where one exists (e.g. PGA at 2 % in 50 years) | A single probability scale across perils |
| **Exposure** | Address points, matched building footprints, user-supplied values | A substitute for a surveyed schedule of values |
| **Vulnerability** | Hazus fragility (earthquake) and depth-damage (flood) functions keyed by the matched structure's type, design level, storeys and foundation; flat documented ratios where no structure is matched | Surveyed building data or a validated proprietary vulnerability model |
| **Financial loss** | Scenario gross loss = exposed value × damage ratio | Expected annual loss (not computed: no event frequencies or vulnerability curves) |
| **Insured loss** | Net of deductible/limit when the user supplies them | A premium |

The composite 0–100 index is a **screening/prioritisation score** with documented,
assumed weights. It is never presented as an event probability.

## What is in the box

```
catrix/
├── backend/            FastAPI + SQLAlchemy/PostGIS + pipeline (Python 3.12)
│   ├── catrix/
│   │   ├── ingest/     dataset connectors (OpenAddresses, CAL FIRE, CGS, USGS, FEMA, Census, Microsoft)
│   │   ├── geo/        CRS handling, validation, spatial joins, raster sampling
│   │   ├── hazards/    11+ hazard modules with one output contract + coverage status
│   │   ├── ml/         location-year dataset, leakage checks, baselines → XGBoost, calibration, SHAP
│   │   ├── scoring/    transparent composite screening index
│   │   ├── finance/    scenario losses with assumption provenance
│   │   ├── portfolio/  CSV validation, enrichment job, aggregation, export
│   │   ├── quality/    data-quality checks
│   │   ├── api/        versioned REST API (/api/v1, OpenAPI at /api/docs)
│   │   └── pipeline/   `catrix` CLI and background worker
│   ├── alembic/        migrations (PostGIS schema)
│   └── tests/          unit (mocked HTTP), integration (real PostGIS), live (opt-in endpoints)
├── frontend/           React 18 + TypeScript + Vite + Tailwind + MapLibre + Recharts + TanStack Query
├── data_sources.yaml   dataset registry: provenance, verification status, licences
├── data/samples/       example portfolio CSV
├── docs/               architecture, provenance, schema, ML methodology, limitations
├── docker-compose.yml  PostGIS + API + worker + frontend (+ optional MLflow)
└── PROGRESS.md         what works, what was executed, blockers, next tasks
```

## Datasets

All sources are public and registered in [`data_sources.yaml`](data_sources.yaml) with URL,
format, CRS, coverage, licence and a **verification status** recorded on 2026-10-13:

| Source | Role | Verified from dev environment |
|---|---|---|
| OpenAddresses (us/ca/*) | property locations | listing yes; downloads need a free token (HTTP 403 otherwise) |
| Census TIGER/Line counties 2023 | regions | yes |
| Microsoft US Building Footprints (California) | building geometry/density | yes (466 MB zip) |
| CAL FIRE FRAP fire perimeters (23,334 polygons) | wildfire history and ML labels | yes |
| CAL FIRE Fire Hazard Severity Zones (2024/25 combined) | wildfire overlay | yes |
| CGS MS48 PGA 2 %/50 yr (NSHM 2023) + USGS ASCE 7-16 service | earthquake ground motion | yes |
| CGS Quaternary faults, MS58 landslide susceptibility, tsunami hazard area | secondary hazards | yes |
| USGS 3DEP 1-arc-second DEM tiles, EPQS | elevation/slope/aspect | yes |
| USGS ComCat earthquake catalogue | seismic history | yes |
| USACE National Structure Inventory | building type, storeys, year built, foundation, modelled value (Hazus vulnerability) | yes |
| PRISM annual climate grids, US Drought Monitor county statistics | year-matched climate and drought ML features | yes |
| LANDFIRE fuel model / canopy cover (LFPS), MRLC NLCD epochs (WCS) | year-matched fuel and land-cover ML features | yes (LF2016+; NLCD 2001-2021) |
| FEMA NFHL flood zones, FEMA National Risk Index | flood overlay, regional benchmarks | **no** — host reset TLS connections from the dev network; schema taken from FEMA documentation |
| NOAA Atlas 14, NOAA GSOM, Census ACS | optional features/context | not probed |
| MTBS, LANDFIRE LF2001-2014, CGS Seismic Hazard Zones | optional rasters/zones | manual download (`local_only`) |

Unavailable sources are reported as `unavailable` or `outside_coverage` on every property;
nothing is substituted with fabricated values.

## Quick start

```bash
cp .env.example .env
docker compose up --build            # PostGIS, API (:8000), worker, frontend (:5173)
```

Then, in another shell (or `docker compose exec api ...`):

```bash
pip install -e "backend[dev]"       # from the repository root
catrix db upgrade                    # run every catrix command from the repository root
catrix ingest all --sample 15000     # counties → addresses → perimeters → layers → faults → events → NRI → buildings → DEM → NSI → climate/drought/fuel/land-cover features → environment
catrix assess                        # every hazard module over every address
catrix score                         # composite screening index
catrix ml build-dataset && catrix ml train && catrix ml predict
catrix quality
```

OpenAddresses batch downloads require `CATRIX_OPENADDRESSES_TOKEN` (free account) **or**
a downloaded `source.geojson.gz` / CSV placed in `data/raw/openaddresses_ca/`.

Full instructions: [docs/SETUP.md](docs/SETUP.md).

## Reproducing the ML experiment

```bash
catrix ml build-dataset    # location-year table (features ≤ T, label = perimeter in T+1), chronological + spatial-block splits
catrix ml train            # baseline, logistic regression, random forest, XGBoost; tuned on validation; isotonic calibration; test untouched
catrix ml predict          # calibrated probabilities + SHAP for every address
```

Outputs land in `data/models/<version>/metrics.json` and the `model_versions` table, and are
rendered on the **ML Model Analytics** page. Method, leakage controls and limitations are in
[docs/ML_METHODOLOGY.md](docs/ML_METHODOLOGY.md). If the data cannot support a credible
model (fewer than 100 training positives), training reports `insufficient_data` and the
dashboard says so instead of showing numbers.

## API

Versioned under `/api/v1` with OpenAPI docs at `/api/docs`:

`GET /health` · `GET /data-sources` · `GET /properties` · `GET /properties/{id}` ·
`GET /properties/{id}/hazards` · `GET /properties/{id}/predictions` ·
`GET /properties/{id}/financial` · `GET /properties/geojson` · `GET /hazards` ·
`GET /hazards/{id}/geojson` · `GET /portfolio/summary` · `POST /portfolio/upload` ·
`GET /portfolio/{id}` · `GET /portfolio/{id}/analysis` · `GET /portfolio/{id}/export` ·
`GET /models/metrics` · `GET /data-quality` · `GET /jobs/{id}`

Spatial queries are bounded (bbox area cap, page limits, simplified geometries by zoom).

## Dashboard

Six pages: Executive Overview, California Hazard Map (clustered points, FHSZ / flood /
tsunami / perimeter overlays with legends and units), Property Risk Explorer (hazard profile,
ML prediction with SHAP, environment, composite breakdown, financial scenarios), ML Model
Analytics (comparison table, ROC/PR/calibration, confusion matrix, importance, SHAP,
geographic performance, limitations), Portfolio Analysis (CSV upload → enrichment job →
map, concentration, top exposures, county summaries, scenarios, export) and Data Quality &
Sources (provenance, versions, ingestion state, coverage, errors, unavailable sources).

Screenshots will be added once the stack has been run (`docs/screenshots/`).

## Operations, recovery and security

- **Assessment refreshes are versioned and atomic.** `catrix assess` and `catrix score`
  stage their rows under an `assessment_runs` entry and swap them into
  `property_hazard_assessments` in one transaction at the end, so the API never serves a
  half-refreshed table and a crash leaves the previous assessment intact. Every live row
  carries the `run_id` that produced it; `GET /api/v1/jobs/assessment-runs` lists runs.
- **Portfolio jobs are leased and retried.** A job is claimed atomically (pending, or an
  expired lease), heartbeats as it progresses, and is returned to the queue when its
  runner dies, up to `max_attempts` (3) before it is marked failed. The worker sweeps
  stale leases on every poll; the API does so at startup. `GET /api/v1/jobs/{id}` shows
  attempts, lease and last error.
- **The API is local-only by default.** It binds to a trusted network with CORS limited to
  the dashboard origins (`CATRIX_ALLOWED_ORIGINS`). Set `CATRIX_API_KEY` to require an
  `X-API-Key` header on every route except `/health` (constant-time comparison); put a
  TLS-terminating reverse proxy in front of it for anything beyond a workstation. Uploads
  are capped at 25 MB and 100,000 rows; spatial queries are bounded by `max_bbox_area_deg2`.
  There is no per-user auth or audit log: this is a screening tool for an analyst's desk,
  not a multi-tenant service.

## Tests

```bash
cd backend                                 # tests and linters run inside backend/
pytest tests/unit -q                       # no network, no database
CATRIX_TEST_DATABASE_URL=postgresql+psycopg://catrix:catrix@localhost:5432/catrix_test pytest tests/integration -m integration
pytest tests/live -m live                  # opt-in: real endpoints
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/DATA_PROVENANCE.md](docs/DATA_PROVENANCE.md)
- [docs/DATABASE_SCHEMA.md](docs/DATABASE_SCHEMA.md)
- [docs/ML_METHODOLOGY.md](docs/ML_METHODOLOGY.md)
- [docs/LIMITATIONS.md](docs/LIMITATIONS.md)
- [docs/SETUP.md](docs/SETUP.md)
- [PROGRESS.md](PROGRESS.md)

## Licence

Code: MIT. Data: each dataset keeps its own licence (public domain, ODbL, CAL FIRE/CGS
terms) as listed in `data_sources.yaml`; raw data is never committed.

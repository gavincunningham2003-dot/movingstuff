# Setup and reproduction

## Prerequisites

- Docker Desktop (for PostGIS and the full stack) **or** a local PostgreSQL 16 with PostGIS 3.4
- Python 3.12 (backend outside Docker)
- Node.js 20 (frontend outside Docker)
- ~3 GB free disk for cached datasets (building footprints 466 MB, DEM tiles ~28 MB each)

## 1. Configuration

```bash
cp .env.example .env
```

Key variables (`CATRIX_` prefix): `DATABASE_URL`, `DATA_DIR`, `ADDRESS_SAMPLE_SIZE`,
`OPENADDRESSES_TOKEN`, `HTTP_MAX_RETRIES`, `RANDOM_SEED`.

## 2. Database

```bash
docker compose up -d db
cd backend && pip install -e ".[dev]"
catrix db upgrade          # alembic upgrade head (creates the PostGIS extension and 20 tables)
```

## 3. Address data (OpenAddresses)

Either set `CATRIX_OPENADDRESSES_TOKEN` (free account at https://batch.openaddresses.io,
then copy the session token) or download county files manually and place them in
`data/raw/openaddresses_ca/` (`*.geojson.gz`, `*.geojson` or legacy `*.csv`).

## 4. Ingestion (Milestones 1–2)

```bash
catrix ingest all --sample 15000
# or step by step:
catrix ingest counties
catrix ingest addresses --sample 15000 --county us/ca/alameda --county us/ca/butte ...
catrix ingest perimeters --min-year 1950
catrix ingest layer wildfire_fhsz
catrix ingest layer tsunami
catrix ingest layer flood_nfhl        # unverified host; or drop an NFHL GDB in data/raw/fema_nfhl/
catrix ingest layer liquefaction      # requires CGS SHZ shapefiles in data/raw/cgs_seismic_hazard_zones/
catrix ingest faults
catrix ingest events
catrix ingest nri                     # unverified host; or drop NRI_Table_Counties.csv/zip in data/raw/fema_nri_counties/
catrix ingest buildings               # 466 MB download, filtered to sampled counties
catrix ingest elevation --max-tiles 40
catrix ingest nsi                     # USACE structure inventory (building attributes); --include-large for LA/SD/Orange...
catrix ingest features                # PRISM climate, USDM drought, LANDFIRE fuels (LFPS jobs), NLCD land cover (WCS)
catrix ingest environment
```

`ingest features` runs the four year-matched feature connectors in turn; each one may take
a while (PRISM downloads ~70 CONUS grids, LANDFIRE jobs are asynchronous and polled every
15 s, NLCD fetches one WCS clip per 0.5-degree tile and epoch). They can also be run
individually: `catrix ingest prism`, `drought`, `landfire`, `nlcd`. If the PRISM host fails
TLS validation behind a corporate proxy, set `CATRIX_HTTP_CA_BUNDLE` to your CA file.

Each connector prints `ingested | unavailable | failed` with the reason; the same state is
visible at `GET /api/v1/data-sources`.

## 5. Assessment and scoring

```bash
catrix assess                  # all hazard modules (point services are cached)
catrix score                   # composite screening index
catrix quality                 # data-quality checks
```

## 6. Machine learning (Milestone 3)

```bash
catrix ml build-dataset
catrix ml train
catrix ml predict
```

## 7. API and frontend

```bash
catrix api --reload                     # http://localhost:8000/api/docs
cd ../frontend && npm install && npm run dev   # http://localhost:5173 (proxies /api)
```

Or everything at once: `docker compose up --build`.

## 8. Tests

```bash
cd backend
pytest tests/unit -q
CATRIX_TEST_DATABASE_URL=postgresql+psycopg://catrix:catrix@localhost:5432/catrix_test pytest tests/integration -m integration
pytest tests/live -m live     # network
ruff check . && mypy catrix
```

Create the test database once: `docker compose exec db psql -U catrix -c "CREATE DATABASE catrix_test"`.

## 9. Portfolio demo

Upload `data/samples/example_portfolio.csv` on the Portfolio page (it includes one
duplicate location and one invalid row on purpose) or:

```bash
curl -F file=@data/samples/example_portfolio.csv -F name=demo http://localhost:8000/api/v1/portfolio/upload
```

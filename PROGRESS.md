# PROGRESS

Last updated: 2026-10-13

## Environment facts

- Windows 11, Python 3.12.10 available; Node.js **not** installed; Docker CLI present but
  the daemon was not started (per instruction: no databases or services were set up).
- Internet access available for read-only verification of data endpoints.

## What was executed

| Step | Executed? | Result |
|---|---|---|
| Endpoint verification (HTTP GET/HEAD against every registry source) | **Yes** | See table in `data_sources.yaml`; details below |
| Byte-compilation of all 91 backend Python files | **Yes** | OK |
| Backend unit tests (`pytest tests/unit`) | **Yes** | 58 passed (see "Test results") |
| Ruff lint + format check | **Yes** | clean |
| Alembic migration | **No** | No database provisioned |
| Data ingestion (`catrix ingest all`) | **No** | No database; OpenAddresses requires a token |
| Hazard assessment / scoring | **No** | Depends on ingestion |
| ML dataset build / training / prediction | **No** | Depends on ingestion. **No metrics exist.** |
| Frontend build (`npm run build`) | **No** | Node.js not installed |
| Docker Compose startup | **No** | Not started by instruction |

## Datasets acquired

None were downloaded into `data/raw` (no pipeline run). Endpoint verification on
2026-10-13 established:

- CAL FIRE `California_Historic_Fire_Perimeters/FeatureServer/0`: 23,334 features, fields
  confirmed (YEAR_, FIRE_NAME, ALARM_DATE, GIS_ACRES, …), EPSG:3857, maxRecordCount 2000.
- CAL FIRE `fhsz24_5/FeatureServer/0` (combined SRA+LRA) and `FHSZSRA_23_3`: fields
  confirmed; point query at (-120.65, 38.75) returned a zone.
- CGS `MS48_GroundMotion_PGA_2pc50/ImageServer`: `getSamples` at (-122.18, 37.85) = 1.253 g.
- CGS `MS58_LandslideSusceptibility_Classes/MapServer`: identify returned class 7 at the
  same point; class 0 in downtown San Francisco.
- CGS `FAM_QFaults`, `IW_Tsunami_Hazard_Area`: schemas confirmed.
- USGS ASCE 7-16 service (`/ws/building-codes/asce7-16/calculate`): returns `pgauh`,
  `pga`, `ss`, `s1`; the older `/ws/designmaps/` URL 301-redirects to it.
- USGS 3DEP tile `USGS_1_n38w123.tif`: 28.5 MB, reachable. EPQS returns elevation.
- USGS ComCat: 2,847 M4+ events in the California bbox since 1980.
- Census TIGER 2023 counties zip: 83 MB, reachable.
- Microsoft building footprints: `minedbuildings.z5.web.core.windows.net/.../California.geojson.zip`
  466 MB reachable; the old `usbuildingdata.blob.core.windows.net` URL returns 409.
- OpenAddresses: `/api/data` listing works (e.g. us/ca/alameda job 926561, 27.5 MB); job
  output download returns **HTTP 403 without a token**.
- FEMA `hazards.fema.gov` (NFHL and NRI): every connection reset — unverified.
- MTBS direct download: JavaScript app behind an antibot form — manual download only.

## Additions on 2026-10-13 (second pass)

- **Building-level vulnerability**: USACE National Structure Inventory connector
  (`catrix ingest nsi`, verified endpoint), `structures` / `address_structures` tables
  (migration 0002), Hazus equivalent-PGA fragility and FIA depth-damage functions in
  `catrix/finance/vulnerability.py` with the transcribed reference tables in
  `hazus_tables.yaml` (verify flag), building-aware scenarios at the 2%/50y and the new
  10%/50y PGA (new `earthquake_pga_10pc50` module), flood depth from NFHL BFE minus NSI
  first-floor elevation, NSI structure value as an externally-derived exposed value, and
  structure attributes on the property API and the Property Explorer page.
- **Year-matched fuel and climate features**: PRISM (annual precipitation / Tmax, 1991-2020
  anomalies), US Drought Monitor (county D2+/D3+ yearly stats), LANDFIRE FBFM40 + canopy
  cover via LFPS jobs (LF2016+, local older versions honoured) and NLCD epochs via WCS
  (2001-2021), each with a `*_version_lag_years` feature; merged into the location-year
  table by `catrix/ml/optional_features.py` and recorded in `metrics.json`.
- Verification of the new endpoints is logged in `docs/DATA_PROVENANCE.md`; PRISM needed an
  unverified-TLS probe because of the development network's certificate interception.
- Unit tests: 74 passing (16 new) in the isolated venv; ruff clean.

## Completed functionality (code)

- Typed configuration, structured logging, dataset registry with provenance.
- PostGIS schema (20 tables, spatial indexes) + Alembic migration.
- Connectors: counties, OpenAddresses (token or local file), CAL FIRE perimeters (paged),
  generic ArcGIS polygon layers (FHSZ, tsunami, NFHL, liquefaction-local), faults, USGS
  events, NRI, Microsoft footprints with address matching + uncertainty, 3DEP terrain with
  Horn slope/aspect, derived environment features (NLCD/LANDFIRE optional local rasters).
- 15 hazard modules sharing one output contract with coverage status, units, horizon,
  confidence and caveats; batch assessment with PostGIS VALUES-list lookups.
- Composite screening index with family-level correlation handling, missing-hazard
  reporting and assumption labelling.
- Financial scenario module (gross/net, assumption kinds, no EAL).
- Portfolio: CSV validation with column aliases, duplicate detection, background
  enrichment job, aggregation (county, 10 km concentration cells, top exposures,
  scenario totals), CSV/JSON export.
- ML: location-year dataset (DuckDB), leakage checks, chronological + spatial-block
  splits, baseline/LR/RF/XGBoost, validation-only tuning, isotonic calibration, test-set
  metrics, curves, calibration table, geographic breakdown, SHAP, insufficient-data guard.
- Data-quality checks and report persistence.
- FastAPI v1 routes with OpenAPI, bbox guard rails, zoom-dependent simplification.
- React/TypeScript dashboard (six pages) — written, not compiled.
- Docker Compose, CI workflow, documentation set.

## Test results

Unit tests run without network (respx mocks) or database. Integration tests require
`CATRIX_TEST_DATABASE_URL` and were **not** run (no PostGIS). Live-endpoint tests are opt-in.

| Date | Command | Result |
|---|---|---|
| 2026-10-13 | `pytest tests/unit -q` (isolated venv, Python 3.12.10, Windows) | **58 passed**, 0 failed, 0 skipped (140 s; includes the synthetic end-to-end training test) |
| 2026-10-13 (second pass) | `pytest tests/unit -q` after adding NSI/Hazus and feature connectors | **74 passed**, 0 failed (145 s) |
| 2026-10-13 | `ruff check . && ruff format --check .` | clean (91 files) |
| 2026-10-13 | `python -m compileall catrix alembic tests` | OK |
| 2026-10-13 | `pytest tests/integration -m integration` | not run (no database) |
| 2026-10-13 | `mypy catrix` | not run |

First run surfaced four failures, all fixed: a numpy `.astype("Int64")` call in the
building matcher (code bug), and three test-data issues (synthetic ML table violated the
monotone "ever inside perimeter" leakage check, which proved the check works; the 0,0
coordinate row being double-counted as "outside California"; a dedup test value on a
rounding boundary).

## Outstanding blockers

1. **OpenAddresses token** (`CATRIX_OPENADDRESSES_TOKEN`) or a manual download is needed
   for real address points.
2. **FEMA hosts** unreachable from the authoring network; NFHL/NRI connectors must be
   validated on first run from another network (or use local NFHL GDB downloads).
3. **No database provisioned** here, so nothing downstream of ingestion has been run.
4. **Node.js** absent, so the frontend has not been type-checked or built.

## Next implementation tasks

1. Run `docker compose up`, `catrix db upgrade`, `catrix ingest all`, `catrix assess`,
   `catrix score`; record real counts and quality results here.
2. Run `catrix ml build-dataset` and `catrix ml train`; publish real metrics (or the
   insufficient-data explanation) and add screenshots.
3. Validate the NFHL schema live; implement the NHD flowline distance connector.
4. Run `catrix ingest nsi` and `catrix ingest features`; confirm the LFPS job flow and the
   NLCD WCS clip sizes on a real run; verify the transcribed Hazus tables against the
   current manuals.
5. Frontend `npm install && npm run build`; fix any type errors; add screenshots.

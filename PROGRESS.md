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

## Third pass on 2026-10-13 (new Windows laptop, first live run)

The repository was cloned to a second machine and, for the first time, the database and
ingestion were actually run. Environment: Windows 11, Git 2.55, Python 3.12.10, Node.js
24.20 (npm 11.19), Docker Desktop 29.8 (PostGIS 16-3.4 container).

### Fixes needed to build and run

Four defects existed in the committed code that no previous pass could have caught,
because the frontend had never been built and the CLI had never been started:

| File | Problem | Fix |
|---|---|---|
| `backend/pyproject.toml` | `readme = "../README.md"` points outside the package root; setuptools refuses to build, so `pip install -e backend` fails | `readme = "README.md"` with a short `backend/README.md` |
| `backend/pyproject.toml` | typer 0.12 is incompatible with click >= 8.2 (`TypeError: Secondary flag is not valid for non-boolean flag` on every CLI invocation) | pinned `click>=8.1,<8.2` |
| `backend/catrix/logging.py` | `structlog.stdlib.add_logger_name` requires a stdlib logger, but `PrintLoggerFactory` was configured; first log call raised `AttributeError: 'PrintLogger' object has no attribute 'name'`. Unit tests never call `configure_logging`, so they passed | `logger_factory=structlog.stdlib.LoggerFactory()` |
| `frontend/tsconfig.*.json` | `tsBuildInfoFile` set without `composite`; `tsc -b` refuses (TS5069) | added `"composite": true` |
| `backend/catrix/ingest/openaddresses.py` | the local-file scan accepted any `*.json`, so on a second run the connector's own `*.meta.json` cache files were parsed as GeoJSON (`JSONDecodeError`) | exclude `*.meta.json` |
| `addresses.dedup_key` (schema) | the key embeds the full street name; real OpenAddresses data overflowed `VARCHAR(64)` (`StringDataRightTruncation`) | migration `0003_widen_dedup_key` → `VARCHAR(512)`, model updated |
| `frontend/package.json` | react-map-gl 7.1 pulls its own `@maplibre/maplibre-gl-style-spec` 19.x while maplibre-gl 4.7 uses 20.x; the two type trees are incompatible (15 TS2322 errors in HazardMap, PortfolioAnalysis, PropertyMiniMap) | npm `overrides` pinning the style-spec to 20.4.0; `package-lock.json` now committed |

Note: `catrix` must be run from the **repository root** (the registry path resolves to
`data_dir.parent / data_sources.yaml` and `.env` is read from the cwd). The Makefile's
`cd backend && catrix ...` recipes do not work as written.

### What was executed

| Step | Result |
|---|---|
| `pip install -e backend[dev]` | OK after the pyproject fix |
| `pytest tests/unit -q` | **74 passed** (219 s) |
| `ruff check . && ruff format --check .` | clean (102 files) |
| `npm install && npm run build` (frontend) | **built** for the first time: 943 modules, 729 kB JS + 802 kB maplibre chunk |
| `docker compose up -d db` | PostGIS 16-3.4 healthy |
| `catrix db upgrade` | migrations 0001 and 0002 applied; 20 tables + 21 geometry columns created |
| `catrix ingest counties` | 58 California counties (TIGER2023, 83 MB zip) |
| `catrix ingest faults` | 38,646 fault lines (CGS FAM_QFaults, 8 pages) |
| `catrix ingest events` | 2,847 M4+ events since 1980 (USGS ComCat) |
| `catrix ingest perimeters` | 18,064 perimeters with year >= 1950 (of 23,334 in the CAL FIRE service; 10 pages of 2,000) |
| `catrix ingest nri` | **unavailable**: `hazards.fema.gov` resets the TCP connection, also with TLS verification off and from `curl`, so this is a network/geo block (FEMA blocks non-US addresses), not certificate interception |
| `catrix ingest addresses` | **14,994 addresses** sampled across the 18 default counties (19 FIPS after point-in-polygon); 18 batch-API files, 307 MB, token works |

Downstream connectors, run after addresses:

| Step | Result |
|---|---|
| `catrix ingest layer wildfire_fhsz` | 18,087 FHSZ polygons clipped to the address bbox |
| `catrix ingest layer tsunami` | **unavailable**: CGS `IW_Tsunami_Hazard_Area` returned HTTP 500 (server-side; retry) |
| `catrix ingest layer liquefaction` | **unavailable** by design: no REST endpoint; needs CGS seismic hazard zone shapefiles in `data/raw/cgs_seismic_hazard_zones/` |
| `catrix ingest layer flood_nfhl` | **unavailable**: FEMA geo-block (see NRI above) |
| `catrix ingest buildings` | 730,523 Microsoft footprints read from the 466 MB California zip within the address bbox; 14,994 address-building matches (13 min) |
| `catrix ingest elevation` | 14,994 terrain samples (elevation, Horn slope, aspect) from 22 USGS 3DEP 1-arc-second tiles, 984 MB; ~90 min, dominated by per-tile sampling |
| `catrix ingest nsi` | 338,051 NSI-2022 structures across the 18 counties (4.7 GB of county GeoJSON); 14,994 address-structure matches |

### Fixes found by running the dashboard

| Area | Problem | Fix |
|---|---|---|
| Basemap (`frontend/src/components/map/basemap.ts`) | CARTO's raster basemap now returns "API KEY REQUIRED" placeholder tiles without a key, so every map was a grey watermark grid | switched to the keyless OpenFreeMap "Fiord" dark vector style; cluster labels use `Noto Sans Regular`, which its glyph server provides |
| Map bbox guard | the Hazard Map requests points from zoom 5, but the API rejected any view over 4 deg^2; the initial statewide view (~390 deg^2 in a laptop pane) always failed with HTTP 413 | the frontend clips the request bbox to the California extent (`clipToCalifornia`) and `max_bbox_area_deg2` is 120 (California is ~99 deg^2); both GeoJSON endpoints already cap feature counts. `test_bbox_guard_rails` updated |

### Fixes found by running the pipeline

| Area | Problem | Fix |
|---|---|---|
| Tsunami layer (`cgs_tsunami_hazard_area`) | only 40 features, but each is an enormous coastal multipolygon (5 features ~ 83 MB GeoJSON); paging at the service's `maxRecordCount` makes it return HTTP 500/504 | new optional `page_size` on `SourceSpec`, threaded through `fetch_layer` → `iter_features`; registry sets 10 for this layer (10 works, 20 fails) |
| Proximity lookups (`hazards/lookup.py`, `ml/dataset.py`, `ml/predict.py`) | `ST_DWithin(geom::geography, …)` cannot use the plain geometry GiST index, so each 500-point batch did a sequential scan over all 18,064 perimeters: one batch ran > 5 min with the database at 100 % CPU (the full assess would have taken many hours) | migration `0004_geography_indexes` adds `gist((geom::geography))` on `wildfire_events`, `earthquake_events`, `hazard_lines`, `structures`; EXPLAIN now shows an index scan (cost 466k → 38 per two points) |

### FEMA geo-block workaround

`hazards.fema.gov`, `msc.fema.gov` and `hazards.geoplatform.gov` all drop connections from
non-US networks (the authoring laptop is in Ireland), while `www.fema.gov` and ArcGIS Online
are reachable. Both FEMA datasets exist on ArcGIS Online with identical schemas:

| Source | Replacement | Change |
|---|---|---|
| `fema_nfhl` | Esri Living Atlas `USA_Flood_Hazard_Reduced_Set_gdb/FeatureServer/0` (S_FLD_HAZ_AR, same fields) | registry `api_url` repointed; the polygon-layer connector is unchanged |
| `fema_nri_counties` | FEMA's own `FEMA_NationalRiskIndex` org: `National_Risk_Index_Counties/FeatureServer/0` (58 CA rows, 467 attributes) | registry gains `api_url`; `nri.py` falls back to the feature service when the zip download fails and no local file exists. NRI 2.0 renamed riverine flooding `RFLD_*` → `IFLD_*` (inland flooding); both prefixes map to `flood_riverine` |

`cgs_seismic_hazard_zones` (liquefaction): `www.conservation.ca.gov` refused connections from
here even over a VPN, but CGS publishes the official zones on ArcGIS Online (owner
`gis_cadoc`): `CGS_Liquefaction_Zones/FeatureServer/0` (2,034 polygons; fields QUAD_NAME,
RELEASED, REVISED, report links), plus `CGS_Landslide_Zones` and `CGS_SHZ_Unevaluated_Areas`.
The registry entry now carries that `api_url` (shapefile drop-in still accepted) and the
polygon-layer spec uses QUAD_NAME as the stored attribute; the module reports
"Liquefaction zone". Ingested 2,031 polygons within the address extent (one 2,000-feature
page is ~34 MB of GeoJSON). Follow-up: wire `CGS_SHZ_Unevaluated_Areas` so "evaluated, not
zoned" can be told apart from "never evaluated", and consider the regulatory landslide zones.

### Landslide lookups batched (MS58)

CGS Map Sheet 58 is a raster behind a MapServer: no feature query, no bulk download (the
Hub "shapefile" export of it returns HTTP 500) and the CGS website hosting the GeoTIFF was
unreachable from here. The first assessment therefore issued one `identify` request per
address. Verified on 2026-10-13 that `identify` accepts an `esriGeometryMultipoint` and
returns one result per input point in input order, with NoData points kept in place
(null `Raster.Value`): 300 points answer in ~12 s, 1000 is refused. New
`mapserver_identify_raster_batch` (POST, 250 points per call, falls back to single-point
queries if a batch's result count ever disagrees) and `post_json`; the landslide module now
resolves all uncached addresses in ~60 requests instead of ~15,000. Unit tests cover
ordering with NoData and the misalignment fallback. The NOAA Atlas 14 rainfall module is
still one request per point (no batch endpoint; the downloadable Atlas 14 grids would be
the equivalent fix).

### First full assessment run: what it revealed

The first `catrix assess` over 14,994 addresses failed at its first write after 44 minutes
(`StringDataRightTruncation` on a VARCHAR(32): hazard `units` strings such as
"class (1=Moderate,2=High,3=Very High)" are longer). Migration `0005_widen_assessment_text`
widens `units`, `method`, `classification` and `coverage_status`. The batch timeline showed
where the time went for one 500-address batch: CGS PGA 2%/50y 6 min, PGA 10%/50y 7 min,
landslide 7 min, NOAA Atlas 14 15 min, everything else < 2 s. At that rate the full run
would have taken ~17 hours. Three changes remove it:

| Module | Before | After |
|---|---|---|
| Earthquake PGA (both horizons) | one ImageServer `getSamples` per address | `imageserver_sample_batch`: multipoint `getSamples`, 250 points per POST, locations echoed back and checked (300 points in ~15 s) |
| Landslide (MS58) | one MapServer `identify` per address | `mapserver_identify_raster_batch` (see above) |
| Extreme rainfall (Atlas 14) | one `cgi_readH5.py` request per address (~1.8 s each) | the Volume 6 GIS grid `sw100yr24ha.zip` (3.6 MB, thousandths of an inch, EPSG:4269, nodata -999) is downloaded once and sampled locally (`sample_value`); the point service remains the fallback, both share the point cache. The .asc grid is read into memory once (`_load_grid`, lru-cached): windowed reads of an ASCII raster re-scan the file and cost ~50 s per 500 points; in-memory indexing is ~10 ms |

### LANDFIRE Product Service moved

`lfps.usgs.gov/arcgis/rest/.../GPServer/.../submitJob` now returns the LFPS web app's HTML
(the connector saw "response was not JSON" for LF2016/2022/2023/2024). The replacement is a
JSON API: `GET /api/job/submit?Email=&Layer_List=&Area_of_Interest=&Output_Projection=`
returns `{jobId, status}`; `GET /api/job/status?JobId=` returns `{status, outputFile,
messages}`; `/api/products` lists 138 layers (LF2016, LF2022-LF2025 FBFM40/CC confirmed). A
test job over a 0.1-degree box succeeded in under a minute. The connector is ported and a
contact e-mail is now mandatory: new setting `CATRIX_LANDFIRE_EMAIL` (unset → the fuel
connector reports itself unavailable with that message).

With the e-mail set, `catrix ingest landfire` ran all four editions (LF2016, LF2022, LF2023,
LF2024) over the full 18-county extent: ~15-17 min per job on the USGS side, 2.8 GB of
FBFM40 + canopy-cover rasters under `data/raw/landfire_fuels/`, 59,976 per-address
fuel-feature rows (`fuel_features.parquet`).

### GPU training

The authoring laptop has an RTX 4070 (8 GB, driver 617). The XGBoost 2.1.4 pip wheel
includes CUDA support, verified with a direct `device="cuda"` fit. A new setting
`CATRIX_ML_DEVICE` (`cpu` default, `cuda` in the local `.env`) is passed to every
`XGBClassifier` candidate in `catrix/ml/train.py`. `tests/unit/test_ml_train.py` passes with
`CATRIX_ML_DEVICE=cuda` (7.7 s for the synthetic end-to-end run).

Running the API from the repository root with `--reload` is not advisable: uvicorn's
file watcher picks up every file the ingests write under `data/` and restarts the server
continuously. `.claude/launch.json` starts it without `--reload`.

### First model: dataset and metrics (2026-10-13, GPU)

`catrix ml build-dataset`: 360,731 perimeter-neighbour rows (10 km, PostGIS with the new
geography index, ~5 min) → 434,826 location-years for 14,994 addresses, feature years
1995-2023, **462 positives (prevalence 0.106 %)**. Optional climate (PRISM, 100 % coverage)
and drought (USDM, 83 %) columns were present; LANDFIRE fuels and NLCD land cover were
still ingesting and are not in this version.

`catrix ml train` (XGBoost on CUDA; the three XGBoost candidates fitted in ~2 s each,
the whole run including baselines, calibration and SHAP in 47 s). Splits: train 1995-2014
(264,560 rows), validation 2015-2018 (52,912), test 2019-2023 (66,140), spatial holdout 13
× 50 km blocks (51,214). Selected on validation PR-AUC: **xgboost** (lr 0.05, depth 4),
version `wf-202610132009`.

| Model | Test ROC-AUC | Test PR-AUC (prev. 0.0012) | Spatial ROC-AUC | Spatial PR-AUC (prev. 0.0038) |
|---|---|---|---|---|
| historical frequency baseline | 0.563 | 0.0014 | 0.626 | 0.0056 |
| logistic regression | 0.872 | 0.0046 | 0.659 | 0.0058 |
| random forest (depth 8) | 0.905 | 0.0070 | 0.846 | 0.0155 |
| **xgboost** | **0.912** | **0.0089** | **0.878** | **0.0209** |

Reading: ranking quality is good (ROC-AUC ~0.9 on both the later years and unseen 50 km
blocks) and PR-AUC is 5-7x the prevalence, but the event is rare enough that the
validation-chosen operating threshold (0.0215) yields 0 true positives on the test years
(80 positives, 30 false positives). Brier is at the prevalence floor (0.0012), i.e. the
calibrated probabilities are honest but the signal is weak in absolute terms. Treat it as a
screening ranker, not a classifier at this threshold. Top SHAP features: elevation,
Tmax anomaly, distance to nearest perimeter. Retrain once the LANDFIRE and NLCD features
land.

### Click-anywhere assessment and ad-hoc point features (2026-10-13)

* **`GET /api/v1/hazards/point?lon=&lat=`** assesses any coordinate on demand: county by
  point-in-polygon, every hazard module, the wildfire model with SHAP, and the composite
  screening index, in the same shapes the property endpoints return; nothing is persisted.
  2-6 s per call (the first call for a new 1-degree DEM tile downloads it). The Hazard Map
  now calls it when the user clicks an empty spot and shows the result in a popup
  (`PointAssessmentCard`), with a hint in the status overlay.
* **Static features for ad-hoc points** (`catrix/ml/point_features.py`): the model's
  elevation, slope, northness/eastness and buildings-within-100 m columns existed only
  for the ingested address sample, so portfolio uploads and point queries were predicted
  with NaN for five features (elevation is the strongest SHAP feature). They are now
  derived on the fly from the cached 3DEP tiles and a PostGIS footprint count (bbox
  prefilter + geography distance) inside `predict_for_points`.
* **Model inference on CPU.** A CUDA-resident booster used from uvicorn's worker threads
  crashed the API process with a native fail-fast (exit 0xC0000409) on the second
  request. `load_selected_model` now sets `device="cpu"` on the loaded estimator; training
  still uses `CATRIX_ML_DEVICE`.

### Full assessment, scoring and the second model (2026-10-13, late)

`catrix assess` over all 14,994 addresses completed in ~25 min (≈45 s per 500-address
batch) once the three slow modules were batched or served locally; `catrix score` wrote
14,994 composite rows; `catrix quality` passed every coverage check except liquefaction
(5,500 addresses assessed before the CGS polygons had finished loading; re-assessed
afterwards) and flagged a 9 % building-match shortfall (1,353 addresses without a footprint
within tolerance). Coverage highlights: 3,008 addresses in a Fire Hazard Severity Zone
(2,015 Very High), 2,455 inside a mapped NFHL flood polygon, 3,634 in landslide
susceptibility class ≥ 1, 919 in a liquefaction zone, 8,613 in a tsunami hazard area.

Second model, version `wf-202610132123`, trained after LANDFIRE and NLCD landed (29
features, all optional columns at 100 % coverage except drought at 83 %):

| Model | Test ROC-AUC | Test PR-AUC | Spatial ROC-AUC | Spatial PR-AUC |
|---|---|---|---|---|
| logistic regression | 0.896 | 0.0060 | 0.637 | 0.0064 |
| **random forest** (selected on validation PR-AUC) | **0.948** | **0.0135** | 0.835 | 0.0117 |
| xgboost | 0.925 | 0.0092 | 0.727 | 0.0081 |

Compared with the first version (xgboost: test ROC 0.911 / PR 0.0089, spatial ROC 0.877 /
PR 0.0209) the temporal test improved and the spatial holdout got worse. The selected
model's top SHAP feature is `fuel_version_lag_years`, i.e. the gap between the feature
year and the nearest available LANDFIRE edition. That column is a deliberate anachronism
flag, but it is also a near-monotone proxy for the calendar year, so the model is partly
learning the time trend in perimeter frequency rather than fuel conditions. Treat the
spatial-holdout drop as the honest number and consider dropping the lag columns (or
restricting training to years with a true edition) before quoting the test improvement.

### Sweep after the first full run (2026-10-13, late)

Every GET endpoint returns 200 (health, hazards catalogue and GeoJSON layers, point
assessment, properties list/detail/hazards/predictions/financial, model metrics, portfolio
summary, data sources, data quality); the full unit suite passes (ruff clean). Fixed on the
way:

* `GET /properties/{id}` raised `TypeError: PropertyDetailOut() got multiple values for
  keyword argument 'composite_score'` (the summary dump already carried the key).
* The property page's composite "Contributions" list showed dashes: the UI read
  `contribution`/`weight` while the scorer emits `weighted_points`/`family_weight`.
* The overview's "In SFHA" card showed 0: `isSfhaZone` matched raw FEMA zone codes while
  the flood module labels classes descriptively.
* Provenance: sources consulted per point (CGS PGA x2, MS58, Atlas 14) and the bundled
  Hazus tables showed "never ingested". `mark_lookup_sources` (run at the end of every
  assessment) now records them with their cache sizes. Sources without a connector are
  flagged `planned` (FHSZ SRA 2023 cross-check, MTBS, NHD flowlines, ACS housing) and GSOM is
  `superseded_by: prism_climate`; the data-quality page no longer lists them as unavailable.
  Remaining genuinely unused registry entries: those five, by design.

### Operating points, training scenarios and model versions (2026-10-13, late)

The confusion matrix shown for every model used one threshold, the F1-optimum on the
validation years, which at 0.1 % prevalence transferred to the test years as 0 true
positives. Changes:

* `evaluate.operating_points` / `confusion_at` / `confusion_top_share` / `threshold_sweep`:
  every model now records named operating points chosen on validation (F1-optimal,
  recall >= 25/50/75 %) and share-based points ("flag the riskiest 1/5/10 %") plus a
  flagged-share sweep (0.1 %...50 %) on both held-out splits. Share-based points rank by the
  **raw** model score: isotonic calibration maps many scores onto the same probability, so
  quantiles of the calibrated probability collapsed (1 %, 5 % and 10 % all landed on the same
  10.7 % threshold on the first attempt).
* Model Analytics: the confusion card became an operating-point panel (buttons per point,
  a flagged-share slider, later-years vs unseen-regions toggle, lift vs random), defaulting
  to "riskiest 5 %". `GET /models/versions` lists every training run and
  `GET /models/metrics?version=` serves any of them; the page has a run selector and marks
  non-live runs.
* `catrix ml build-dataset --target within_1km` (label: a perimeter of year T+1 within 1 km;
  `DatasetSpec.target`, dataset `wf-ly-1km-1.0`), `catrix ml train --target ...
  --no-lag-features --no-select --version ...`.

Scenario results (all random forest selected):

| Run | Target | Features | Test ROC / PR-AUC | Spatial ROC / PR-AUC | Riskiest 10 % flagged (test) |
|---|---|---|---|---|---|
| `wf-202610132123` | inside | 29 | 0.948 / 0.0135 | 0.835 / 0.0117 | n/a (pre-dates operating points) |
| `wf-nolag-202610132304` (live; re-run of 2247 with ranked operating points) | inside | 27, lag cols dropped | 0.935 / 0.0096 | 0.847 / 0.0133 | riskiest 10 %: 70 of 80 positives caught, lift 8.8x; riskiest 1 %: 6 of 80, lift 7.5x |
| `wf-1km-202610132254` (recorded) | within 1 km | 27, lag cols dropped | 0.834 / 0.0312 | 0.748 / 0.0404 | 270 of 620 caught, lift 4.4x; riskiest 1 %: lift 6.1x |

Reading: dropping the lag columns costs a little temporal ROC and gains a little spatial
ROC, and the top SHAP features become vegetation fraction, elevation, developed fraction
and burnable fraction, i.e. physical drivers rather than a calendar proxy. The within-1 km
target has 7.6x the positives (3,526) and a much more usable precision-recall trade-off
for screening, at the cost of a less specific meaning. Both runs are selectable on the
Model Analytics page; the live model remains the inside-perimeter, no-lag run.

### External review and corrections (2026-10-14, branch `fix/model-validation-and-reliability`)

A line-by-line review of commit e6fa237 listed 16 items; the 12 fixes were verified
against the code and implemented, the 4 improvements are tracked below.

| # | Finding (confirmed) | Fix |
|---|---|---|
| 1 | model selected by **test** PR-AUC | selection and the baseline comparison use validation PR-AUC; `doc["selection"]` records the protocol |
| 2 | years before the first LANDFIRE/NLCD edition were backfilled with a later map | `match_version_year` returns None (features stay NaN); `_expand_periodic` skips those years; `leakage_checks` rejects negative lags; unit tests updated |
| 3 | portfolio rows matched to address features by DataFrame index | `optional_features_for_points` only honours an explicit `address_id` column; `predict_all_addresses` passes it; new unit test |
| 4 | only fuel features were coordinate-sampled for new points | `_landcover_fallback` (NLCD tiles) and `_climate_fallback` (PRISM annual grids + normals) added; terrain/footprints already handled by `point_features.py` |
| 5 | tuning, calibration and thresholds all on validation | hyper-parameters by grouped 3-fold CV inside the training years (`_cv_average_precision`); validation keeps calibration + thresholds; test reported only |
| 6 | retrain after the chronology fix | both datasets rebuilt and both scenarios retrained (results below) |
| 7 | fixed thresholds vs ranking budgets conflated | every operating point carries `kind` (fixed_threshold / ranking_budget), `recall_ci95` (Wilson) and `n_positives`; labels and the dashboard panel say which is which |
| 8 | negative financial values stayed valid | they now invalidate the row; test added |
| 9 | API background task and the worker could both run a job | `claim_job()` atomic pending→claimed; the API claims before running; `CATRIX_JOB_RUNNER=worker` disables the inline path (compose sets it) |
| 10 | Docker image missing README; registry not visible in containers | `COPY README.md`; `CATRIX_REGISTRY_FILE` setting; compose mounts `data_sources.yaml` and sets it for api and worker |
| 11 | Makefile/docs ran `catrix` from `backend/` | all `catrix` recipes and docs run from the repository root; tests/linters stay in `backend/` |
| 12 | 93 mypy errors (CI red) | 0 errors without disabling checks: `rows()` typed helper for itertuples, Literal casts for DB strings, structlog `Processor` annotations, narrowed Optionals |

Also: `/docs` link fixed to `/api/docs`; `.env.example` documents the new settings.

Verification on this branch: ruff clean, mypy 0 errors (77 files), 79 unit tests and 4/4
PostGIS integration tests pass, the frontend production build passes, and
`docker compose build api` succeeds (4.4 GB image); a container started with the compose
mounts loads the 29-source registry and imports the API (27 routes).
Still open from the review (improvements): duplicate exposure handling (#13), API
authentication/rate limiting for public deployment (#14), fully reproducible experiment
scripts and committed final metrics (#16); documentation (#15) is updated in this branch.

### Results after the corrections (2026-10-14)

Both datasets were rebuilt with the no-backfill rule and both scenarios retrained under
the corrected protocol (CV tuning in the training years, calibration and thresholds on
validation, selection by validation PR-AUC, lag columns excluded). Optional feature
coverage is now honest: fuels 28 % of location-years (LF2016 onward only), land cover 79 %
(NLCD 2001 onward), climate 100 %, drought 83 %.

| Run | Target | Selected (by validation PR-AUC) | Test ROC / PR-AUC | Spatial ROC / PR-AUC | Riskiest 10 % review budget (test) |
|---|---|---|---|---|---|
| `wf-fix-202610142028` (live) | inside perimeter | logistic regression (val PR 0.0101; RF 0.0087, XGB 0.0081) | 0.894 / 0.0059 | 0.729 / 0.0075 | 45 of 80 positives, lift 5.6x, recall 95 % CI 45-67 % |
| `wf-fix-1km-202610142050` (recorded) | within 1 km | logistic regression (val PR 0.0396) | 0.805 / 0.0274 | 0.718 / 0.0396 | 201 of 620, lift 3.2x |

For comparison, XGBoost in the live run scores 0.911 / 0.844 ROC-AUC on test / spatial,
but it is not selected because the test years no longer influence the choice; the
earlier runs (0.935-0.948 test ROC-AUC) benefited from selecting on test and from future
vegetation maps. These are the numbers to quote. Predictions for all 14,994 addresses
were regenerated from the live run.

### Stage A — ML correctness, feature policy and portable artefacts (2026-10-14, branch `improve/catrix-risk-intelligence`)

Scope: items 1-4 of the improvement prompt. Everything below was implemented, unit-tested
and then exercised by a real retrain of both targets plus the modern-era experiment.

**Chronological cross-validation (`catrix/ml/cv.py`).** Hyperparameters and the model
family are now chosen by forward-chaining CV inside the training years (1995-2014), with a
one-year gap between each training block and its validation block so that the T+1 target
of a training row can never fall in a validation year. Candidate validation years are split
into three contiguous blocks (2002-2006, 2007-2010, 2011-2014); a fold is skipped (and
recorded as such) when it has fewer than 20 training or 5 validation positives, and training
refuses to proceed if no fold is usable. The out-of-fold predictions feed calibration.

**Calibration from out-of-fold predictions.** None / isotonic / sigmoid are fitted on the
time-aware OOF predictions and the method is picked by Brier score on the 2015-2018
validation years (ties favour "none"). The selected method is recorded per model with the
reliability tables for all three. Thresholds are still read off validation; test and
spatial holdout are reported only. `evaluation_caveats` in every metrics document states
that the test period has been inspected repeatedly during development.

**Feature policy (`dataset.py`).** Features are classified mandatory (fire history, terrain,
buildings), optional (climate, drought, land cover) and experimental (LANDFIRE fuel columns,
which only exist from LF2016). `--features default|all|mandatory` and `--min-coverage`
select them; the decision for every feature (kind, coverage per split, included/excluded,
reason) is stored as `feature_selection`, and `feature_availability` records coverage by
split. A `modern` dataset spec (feature years 2016-2023, train 2016-2019 / val 2020-2021 /
test 2022-2023) exists for the fuel-feature experiment. Lag columns are kept on prediction
rows for the freshness report but excluded from the model.

**Portable artefacts (`catrix/ml/artifacts.py`).** Metrics documents name joblib files
relative to the run directory (`artifact_file`; legacy absolute `artifact_path` resolved by
file name), and carry `feature_schema` (name, kind, dtype, coverage), `preprocessing_version`
(`features-v2`), `training_config` (dataset, target, policy, seed, device, CV settings),
`dependency_versions` and a deterministic `experiment_id`. Loading validates all of this and
raises `ArtifactIncompatibleError` (wrong preprocessing version, missing schema/selected
model, different scikit-learn/XGBoost major); a minor-version difference is a warning.
`load_selected_model()` returns None with a logged reason instead of serving a broken model.

**Prediction quality report.** `/properties/{id}` and `/hazards/point` now return
`feature_completeness`, `missing_features`, `data_freshness` (feature year, climate year,
land-cover epoch, fuel edition, fire history through), `coverage_warnings`, a
`quality` label (complete ≥ 95 %, partial ≥ 70 %, sparse) and the top five SHAP factors
with an explanation that this is a screening probability, not damage, claim, loss or
premium. The property page renders this as a "Prediction inputs" block; the map popup
shows "inputs complete (100 %)".

**Retrain under the new protocol (real runs, RTX 4070, 2026-10-14 22:05-22:28):**

| Run | Target | Folds used | Chronological-CV PR-AUC (selection) | Calibration | Test ROC / PR-AUC / Brier | Spatial ROC / PR-AUC | Riskiest 10 % budget (test) |
|---|---|---|---|---|---|---|---|
| `wf-chrono-202610142205` (live) | inside perimeter | 2 of 3 (2011-2014 fold skipped: < 5 validation positives) | **LR 0.0086**, XGB 0.0051, baseline 0.0044, RF 0.0037 | sigmoid (Brier 0.001451 vs isotonic 0.001452 vs none 0.0707) | 0.901 / 0.0064 / 0.0012 | 0.734 / 0.0080 | 45 of 80 positives, lift 5.6x, recall 95 % CI 45-67 % |
| `wf-chrono-1km-202610142208` (recorded) | within 1 km | 3 of 3 | **LR 0.0219**, RF 0.0191, baseline 0.0179, XGB 0.0117 | isotonic | 0.806 / 0.0274 / 0.0092 | 0.719 / 0.0406 | 201 of 620, lift 3.2x, CI 29-36 % |
| `wf-chrono-modern-202610142220` (experiment, `--features all`) | inside, 2016-2023 | — | — | — | **insufficient data**: 115 training and 9 test positives (needs 100 / 10) | — | — |

Random forest scores higher on the test and spatial sets in the live run (0.918 / 0.844
ROC-AUC) but is not selected: selection no longer looks at those sets, and RF's
chronological-CV PR-AUC is below the baseline's. The fuel-feature experiment cannot be
reported as a model with the current 14,994-address sample; the metrics document records
the reason and the feature-selection decisions. Predictions for all 14,994 addresses were
regenerated from the live run (`catrix ml predict`, 14,994 rows).

Checks: `ruff check` / `ruff format --check` clean; `mypy catrix` clean (79 files);
`pytest tests/unit -q` **85 passed** (233.9 s, GPU) after Stage A alone; frontend `tsc`
clean.

### Stage B — insurance financial logic and duplicate exposure handling (2026-10-14)

Scope: items 5-7 of the improvement prompt.

**CSV contract (`portfolio/validation.py`, served at `GET /portfolio/csv-contract`).**
Only latitude and longitude are required; `property_id` is generated as `row-<n>` when the
column or a cell is missing (counted and reported). Money columns are distinct and no longer
aliased onto each other: `property_value` (building replacement value), `contents_value`,
`insured_value` (sum insured / TIV), `limit` (policy limit) and `deductible`. Headers are
normalised the same way on both sides ("Policy Limit", "policy-limit" and "POLICY_LIMIT"
all match). An empty amount is "not supplied"; a malformed one (`n/a`, text) is a
*field-level warning* carrying row, field and the offending value, the row is kept for
hazard assessment with the value treated as missing; a negative amount is a row-level
error. The frontend column-mapping panel reads the contract from the API (static fallback
mirrors it) and the validation list shows severity, row and field.

**Duplicate kinds.** Rows are classified instead of silently dropped: `exact` (same
property_id at the same location within 5 m — excluded from counts and totals),
`shared_location` (different property_id at the same site — kept and surfaced as location
accumulation), `repeated_id` (same id elsewhere — kept and flagged). `location_key` and
`duplicate_kind` are stored per row; portfolio status reports exact duplicates, shared
locations and distinct locations separately, and the dashboard tiles say which is which.
Previously every location duplicate was excluded from TIV, which hid exactly the
accumulation an underwriter wants to see.

**Financial logic (`finance/scenarios.py`, assumptions version `fs-2.1`).** Gross loss is
physical damage to the *replacement* value (building + contents); the sum insured is a
policy quantity used only as a proxy when no replacement value is known
(`exposed_value_basis`: replacement_value | sum_insured_proxy | nsi_structure | missing).
Net insured loss applies the deductible and the limit, where the limit defaults to the sum
insured when no explicit limit is given (`limit_basis`: policy_limit | sum_insured | none) —
earlier code never applied a limit in portfolio runs. Every scenario carries a damage-ratio
sensitivity (×0.5, ×1.0, ×1.5, ratio capped at 1) for gross and net.

**Portfolio totals (`portfolio/analytics.py`).** Sum insured, building value, contents value,
replacement value, policy limits and deductibles are totalled separately with the number of
exposures contributing to each; scenario totals report gross, net, the number of exposures
with and without terms, and sensitivity totals; `location_accumulation` lists sites with
more than one exposure (ids, policies, sum insured, replacement value, share of TIV). The
export CSV gains `duplicate_kind`, `location_key`, `contents_value`, `policy_limit`,
`deductible` and `exposed_value_basis`.

**Migration `0006_portfolio_exposure_fields`** adds the columns above (deductible moves out of
the `financial` JSON; old rows are back-filled and old location duplicates are relabelled
`shared_location`). Applied to the development database.

**Verification (real run, 2026-10-14 22:35).** Uploaded the regenerated 25-row example CSV
(now with contents, limits, an exact duplicate, a second policy at one site, a malformed
amount and an id-less row) through the restarted API: 24 valid, 1 invalid, 1 exact
duplicate excluded, 1 shared location, 22 distinct locations; totals: sum insured
$17.63 M over 22 exposures, replacement value $26.14 M (building $22.54 M + contents
$3.60 M), limits $14.12 M explicit for 16 exposures with 6 falling back to the sum insured
and 1 with no cap; the Berkeley site shows two policies (POL-B, POL-K) with $2.1 M sum
insured, 11.9 % of TIV. Scenario totals report net below gross with the number of exposures
that had terms. The frontend financial card, accumulation table and severity-tagged
validation list render these values.

Checks: `ruff`, `mypy catrix` (79 files) clean; `pytest tests/unit -q` **92 passed**
(162 s); frontend `npm run build` succeeds. The integration test
`test_portfolio_upload_roundtrip` was updated for the new counts (exact duplicate excluded,
shared location kept) and is run in Stage E with the PostGIS test database.

### Stage C — historical catastrophe stress tests (2026-10-14)

Scope: items 8-10 of the improvement prompt. New module `catrix/portfolio/stress.py`,
three endpoints, a frontend panel, unit and integration tests.

**Backend.** One set-based PostGIS query per request: `portfolio_properties` (exact
duplicates excluded) joined to `wildfire_events` with `ST_Intersects` on the GiST-indexed
perimeter geometry, plus an optional `ST_DWithin` geography buffer (≤ 5 km) for near
misses. `GET /portfolio/{id}/stress-tests` ranks every ingested perimeter that touches the
portfolio by sum insured inside (with exposures inside, near misses, share of TIV, centroid)
and appends a catalogue of 329 fires ≥ 10,000 acres since 2000 so a famous event can be
replayed even when it misses. `GET /portfolio/{id}/stress-tests/{event_id}` returns the
event, a simplified perimeter as GeoJSON, every hit (inside / within buffer, distance, sum
insured, replacement value, exposed value and its basis, deductible, limit, gross, net) and
totals: exposures inside, sum insured and replacement value inside, share of TIV, gross and
net with the number of exposures with and without terms, and gross at ×0.5 / ×1.5 of the
chosen damage ratio. `/export` streams the hits as CSV. The damage ratio is a required
*assumption* (default 0.5, user-set): the perimeter is the burned area, not structure loss,
and every response says so. Near-miss exposures are listed but never totalled. Requests on
an incomplete portfolio get 409; unknown events 404.

**Frontend.** "Historical stress tests" card on the portfolio page: event selector (fires that
hit the book first, catalogue fires marked "misses"), near-miss buffer (0-5 km), damage-ratio
input, ranked event table, five result tiles, a map with the perimeter overlay and hit
locations (red inside, yellow near-miss, click for details), the ranked hit table, CSV
export and the assumptions/caveats list.

**Verification (real run, 2026-10-14 22:45, the 25-row example portfolio).** Seven ingested
perimeters touch the book within 1 km; only the **Camp Fire (2018, 153,336 acres)** contains
an exposure: P-0004 in Paradise, sum insured $380,000 (2.16 % of TIV), replacement value
$525,000 → gross $262,500 at ratio 0.5, net $260,000 after the $2,500 deductible and the
$380,000 sum-insured cap; sensitivity $131,250 / $393,750 at ratios 0.25 / 0.75. The Thomas
(2017) and Medio (2020) fires come within 1 km of an exposure without containing one.

Tests: `tests/unit/test_stress.py` (pure loss arithmetic, export rows, ratio range) and
`test_portfolio_stress_test_replays_a_perimeter` in the PostGIS integration suite (a seeded
2020 perimeter contains one of three exposures: 50 % of TIV, gross 500,000 / net 495,000,
near miss listed with no loss, CSV export, 404 for an unknown event, 409 before completion).
A first integration run failed on `could not determine data type of parameter` for the
nullable `min_year`/`buffer_m` bind parameters (fixed with explicit casts), then on a NaN
composite score reaching the JSON encoder (NaN is now treated as missing both when the
enrichment writes scores and when the stress test reads them). Final run: integration
**5 passed** (28 s), unit **97 passed**, `mypy` clean (80 files), frontend build succeeds.

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
- Financial scenario module (gross on replacement value, net with deductible and limit
  defaulting to the sum insured, damage-ratio sensitivity, assumption kinds, no EAL).
- Portfolio: served CSV contract, optional ids, distinct money columns, malformed-vs-empty
  amounts, classified duplicates (exact / shared location / repeated id), background
  enrichment job, aggregation (county, 10 km concentration cells, location accumulation,
  top exposures, separate money totals, scenario totals with sensitivity), CSV/JSON export,
  historical perimeter stress tests (ranked events, per-hit losses, perimeter GeoJSON, CSV).
- ML: location-year dataset (DuckDB), leakage checks, chronological + spatial-block
  splits, baseline/LR/RF/XGBoost, chronological forward-chaining CV with a gap for tuning
  and selection, OOF calibration (none/isotonic/sigmoid by validation Brier), feature
  policy (mandatory/optional/experimental), portable self-describing artefacts with
  validation on load, test-set metrics, operating points, calibration table, geographic
  breakdown, SHAP, prediction-quality report, insufficient-data guard.
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
| 2026-10-14 | `pytest tests/unit -q` after Stage A (chronological CV, artefacts, feature policy) | **85 passed** (233.9 s, GPU) |
| 2026-10-14 | `pytest tests/unit -q` after Stage B (CSV contract, duplicate kinds, finance) | **92 passed** (162.2 s) |
| 2026-10-14 | `mypy catrix` (Stage A and B) | clean, 79 files |
| 2026-10-14 | `npm run build` (Stage A and B) | succeeds |
| 2026-10-15 | `pytest tests/unit -q` after Stage C | **97 passed** |
| 2026-10-15 | `pytest tests/integration -m integration` (PostGIS `catrix_test`) after Stage C | **5 passed** (28 s) |

First run surfaced four failures, all fixed: a numpy `.astype("Int64")` call in the
building matcher (code bug), and three test-data issues (synthetic ML table violated the
monotone "ever inside perimeter" leakage check, which proved the check works; the 0,0
coordinate row being double-counted as "outside California"; a dedup test value on a
rounding boundary).

## Outstanding blockers

1. ~~OpenAddresses token~~ resolved on 2026-10-13 (third pass): token in local `.env`, 14,994 addresses ingested.
2. **FEMA hosts** unreachable from the authoring network; NFHL/NRI connectors must be
   validated on first run from another network (or use local NFHL GDB downloads).
3. ~~No database provisioned~~ resolved on 2026-10-13 (third pass): PostGIS runs locally and the schema is migrated.
4. ~~Node.js absent~~ resolved: the frontend builds.

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

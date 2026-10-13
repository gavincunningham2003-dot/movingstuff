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

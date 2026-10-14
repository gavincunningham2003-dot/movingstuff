# Wildfire perimeter-exposure model — methodology

## Objective and target

Predict whether a location will lie inside a **mapped CAL FIRE fire perimeter during the
next calendar year**.

- Unit of analysis: *location-year* (address × feature year T).
- Features: information knowable by 31 December of year T.
- Target: `1` if the point intersects ≥ 1 perimeter with `YEAR_ = T + 1`, else `0`.
- Label quality flags: `mapped_modern` (target ≥ 2000) and `mapped_legacy` (1990s), because
  FRAP perimeter completeness improves over time. Absence of a perimeter means *no mapped
  perimeter*, not *no fire*. The model therefore measures historical perimeter exposure,
  **not** building damage or insured loss.

## Features (all available at prediction time)

| Group | Features | Source | Time handling |
|---|---|---|---|
| Fire history | perimeters within 1/5/10 km in the previous 10 y; within 5 km in 30 y; years since last perimeter ≤ 1 km; ever inside a perimeter; acres burned within 5 km in 5 y; distance to nearest perimeter | CAL FIRE FRAP | only `YEAR_ ≤ T` (enforced in one DuckDB query) |
| Terrain | elevation, slope, northness, eastness | USGS 3DEP (Horn 3×3) | static |
| Development | buildings within 100 m | Microsoft footprints (2014-2020 imagery) | static — a mild anachronism for early years, documented |
| Climate | precipitation as % of the 1991-2020 normal (year T, year T-1, 3-year mean), Tmax anomaly | PRISM 4 km annual grids | year T observations only |
| Drought | mean weekly county area in D2+ and max in D3+ during year T | US Drought Monitor county statistics | year T; null before 2000 (never imputed as "no drought") |
| Fuels | FBFM40 code, fuel group (NB/GR/GS/SH/TU/TL/SB), burnable flag, canopy cover, share of burnable pixels within 500 m (WUI proxy), `fuel_version_lag_years` | LANDFIRE LF2016/2022/2023/2024 via LFPS; LF2001-2014 if downloaded manually | latest version with data year ≤ T; earlier years use the earliest version and carry the lag as a feature |
| Land cover | NLCD class, developed intensity (0-4), vegetated and developed shares within 500 m, `lc_version_lag_years` | MRLC NLCD epochs 2001-2021 via WCS | latest epoch ≤ T; the same lag rule |

The four optional groups are present only when their connectors have run (`catrix ingest
features`); `metrics.json` records the non-null share of every optional feature so a reader
can tell which sources actually contributed to a given model version.

Deliberately excluded: present-day FHSZ (post-dates the targets), raw lat/lon (spatial
memorisation), any climate observation for year T+1.

## Dataset construction

1. One PostGIS pass collects, per address, every perimeter within 10 km (`year`,
   `distance_m`, `inside`, `gis_acres`).
2. DuckDB aggregates per (address, T) for T = 1995…2023 (targets 1996…2024).
3. `leakage_checks()` asserts `target_year = feature_year + 1`, monotone
   `ever_inside_perimeter`, and no present-day classification columns.

## Validation design

- **Chronological**: train T ≤ 2014, validation 2015–2018, test 2019–2023 (configurable in
  `DatasetSpec`; final dates must be chosen from the verified perimeter coverage).
- **Spatial block holdout**: 15 % of 50 km EPSG:3310 grid blocks are removed from train
  and validation entirely and reported as `spatial_test`.
- **Geographic breakdown**: test metrics by county (n ≥ 200).
- **Hyper-parameters** (small grid per family) are chosen by grouped 3-fold
  cross-validation *inside the training years* (folds are blocks of feature years), so no
  later data touches tuning.
- **Calibration and thresholds** use the validation years: isotonic regression is fitted
  there, and every named operating point (F1-optimal, recall targets) is a probability
  threshold read off the validation years and applied unchanged to the held-out splits.
- **Model choice** is by validation PR-AUC. The test years and the spatial holdout are
  reported only; changing their labels cannot change which model is selected.
- **No future editions.** Periodic sources (LANDFIRE fuels, NLCD land cover) are matched
  to the latest edition with data year ≤ T. Years before the first available edition get
  missing values, never a later map: a vegetation map collected after year T reflects the
  fires that happened in between and would encode the label. `leakage_checks()` rejects any
  negative edition lag. The `*_version_lag_years` columns are therefore ≥ 0 and are
  excluded from the live model (`--no-lag-features`) because they still track the calendar.

## Models

1. Historical-frequency baseline (isotonic map of the 10-year 5 km perimeter rate).
2. Logistic regression (median impute, standardise, class-weight balanced).
3. Random forest (balanced subsample, depth-limited, min leaf 20).
4. XGBoost (`scale_pos_weight`, depth 4–6, lr 0.05–0.1, 600 trees, `aucpr`).

Rare positives are handled with class weights only — no SMOTE, because location-years
are spatially and temporally dependent and synthetic neighbours would leak.

Selection: highest **validation** PR-AUC among learned models, reported *only if* it beats
the baseline on validation; otherwise the baseline is marked selected and the dashboard
says so.

## Operating points

The dashboard distinguishes two kinds of operating point and labels them as such:

- **Fixed thresholds**: probability cuts chosen on the validation years (F1-optimal,
  recall ≥ 25/50/75 %) and applied unchanged to the test years and the spatial holdout.
- **Review budgets**: "the riskiest N % of *this* evaluation sample" by model rank. These
  answer the screening question ("if we can inspect 5 % of locations, how many next-year
  exposures do we catch?") but are not transferable thresholds. Ranking uses the raw
  model score because isotonic calibration ties probabilities into steps.

Every point reports counts, precision, recall with a 95 % Wilson interval, specificity,
flagged share and lift over prevalence; a flagged-share sweep (0.1 %–50 %) backs the slider.

## Prediction-time features for new locations

Uploaded portfolio rows and map clicks are never matched to an ingested address by
DataFrame index. Only an explicit `address_id` column reuses the per-address tables;
every other point is sampled from its own coordinates: PRISM annual grids and 1991-2020
normals, the latest LANDFIRE edition ≤ T, the NLCD epoch ≤ T (tiles fetched on demand),
the county drought table, the 3DEP terrain tiles and a PostGIS footprint count within
100 m. Anything unavailable is a null feature, reported as such.

## Metrics reported

ROC-AUC, PR-AUC, precision, recall, F1, Brier score, prevalence, confusion matrix at the
documented threshold, ROC and PR curves, quantile-binned reliability table, feature
importance, SHAP summary (mean |SHAP|), per-row SHAP for predictions, spatial-test metrics,
per-county metrics. All are written to `data/models/<version>/metrics.json` and the
`model_versions` table, and served by `/api/v1/models/metrics`.

## Insufficient-data rule

If the training period has fewer than 100 positive location-years or the test period fewer
than 10, `train_all` returns `status = insufficient_data` with an explanation. No model is
selected, no metrics are published and the dashboard displays the reason. Nothing is
fabricated.

## Use in the composite index

The calibrated probability is normalised as `p / 0.10` (capped at 1) inside the wildfire
family, where it is combined by *max* with FHSZ and perimeter-history indicators. This
normalisation is a screening assumption documented in `scoring/composite.py`.

## Results

Live and recorded runs are listed by `GET /api/v1/models/versions` and every number on
the Model Analytics page is read from the run's `metrics.json` (never typed by hand).
`PROGRESS.md` carries the headline figures of each run with the date and the exact
command. The first runs (2026-10-13) pre-date the corrections above and are kept only for
comparison; the live model is the first run trained after them.

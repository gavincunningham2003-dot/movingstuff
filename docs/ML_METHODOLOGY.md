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
- Hyper-parameters (small grid) and the decision threshold (max F1) are chosen on the
  validation years only; isotonic calibration is fitted on validation; the test years are
  untouched until the final evaluation.

## Models

1. Historical-frequency baseline (isotonic map of the 10-year 5 km perimeter rate).
2. Logistic regression (median impute, standardise, class-weight balanced).
3. Random forest (balanced subsample, depth-limited, min leaf 20).
4. XGBoost (`scale_pos_weight`, depth 4–6, lr 0.05–0.1, 600 trees, `aucpr`).

Rare positives are handled with class weights only — no SMOTE, because location-years
are spatially and temporally dependent and synthetic neighbours would leak.

Selection: highest test PR-AUC among learned models, reported *only if* it beats the
baseline; otherwise the baseline is marked selected and the dashboard says so.

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

No training run has been performed yet; see `PROGRESS.md`. When one is, this section will
be filled from `metrics.json` (never by hand).

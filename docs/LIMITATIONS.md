# Known limitations and future improvements

## Limitations

1. **Nothing has been executed end-to-end yet.** The codebase is complete and tested at the
   unit level, but ingestion, assessment and training have not been run in the authoring
   environment (no database). `PROGRESS.md` is the record.
2. **Address ≠ building.** OpenAddresses points are frequently parcel centroids; matching to
   Microsoft footprints is a ≤ 30 m nearest heuristic with a stated uncertainty class.
3. **Perimeter labels are incomplete** (small fires, early decades) and measure extent, not
   damage. Structure-level outcomes (CAL FIRE DINS) are not integrated.
4. **FEMA endpoints unverified** from the authoring network; NFHL/NRI connectors follow the
   published schema and must be validated on first run. Flood coverage gaps are reported as
   `outside_coverage`.
5. **No vulnerability model.** Scenario damage ratios are simplified assumptions or Hazus
   midpoints; building type, year built, storeys and first-floor elevation are unknown.
6. **No expected annual loss.** Event frequencies and vulnerability curves are not
   available at property level; CATRIX refuses to turn screening scores into probabilities.
7. **Composite weights are assumptions.** They are not estimated from losses; the index is a
   prioritisation tool with the contributing measurements always visible.
8. **Climate/fuel features optional.** LANDFIRE, NLCD, NOAA and USDM require manual or
   unverified downloads; the ML model runs without them and says so.
9. **Spatial autocorrelation.** Location-years from neighbouring addresses are not
   independent; the spatial-block holdout quantifies but does not remove the effect.
10. **Point web services** (CGS PGA, USGS design maps, Atlas 14) are rate-limited and slow
    for large portfolios; results are cached by rounded coordinate.
11. **Sea-level-rise scenarios, extreme-heat projections and winter storms** are represented
    only by county NRI benchmarks.
12. **Secondary hazard liquefaction** depends on a local CGS download and cannot distinguish
    "evaluated and not zoned" from "never evaluated" without quadrangle coverage polygons.

## Future improvements

- Integrate CAL FIRE DINS structure-damage inspections for a true damage target.
- Year-matched LANDFIRE fuel models and NLCD land cover for the ML feature table.
- NOAA GSOM / PRISM climate anomalies and USDM drought as leakage-safe annual features.
- NHD flowline distance and HAND-based pluvial proxies for flood.
- Alquist-Priolo fault zones and CGS quadrangle coverage polygons.
- Hazus fragility curves keyed by assessor building attributes where county data allow.
- Vector tiles (PMTiles) for hazard layers instead of per-request GeoJSON.
- Optuna tuning, LightGBM, conformal prediction intervals, MLflow tracking in CI.
- Correlated-event portfolio scenarios (e.g. a single perimeter footprint hitting many
  locations) instead of independent per-property sums.

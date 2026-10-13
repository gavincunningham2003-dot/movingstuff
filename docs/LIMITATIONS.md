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
5. **Vulnerability is modelled, not surveyed.** Where a USACE National Structure Inventory
   record is matched (within 30 m), earthquake and flood scenarios use Hazus fragility and
   depth-damage functions keyed by building type, design level, storeys and foundation.
   NSI attributes are themselves modelled estimates, the Hazus tables in
   `backend/catrix/finance/hazus_tables.yaml` were transcribed by the authors and carry a
   verify flag, PGA is not adjusted for site conditions (Vs30), and wildfire still uses a
   flat screening ratio because no Hazus wildfire fragility exists. Unmatched locations
   fall back to the flat ratios. The largest counties (e.g. Los Angeles) are skipped by
   default because their NSI responses are multi-gigabyte.
6. **No expected annual loss.** Event frequencies and vulnerability curves are not
   available at property level; CATRIX refuses to turn screening scores into probabilities.
7. **Composite weights are assumptions.** They are not estimated from losses; the index is a
   prioritisation tool with the contributing measurements always visible.
8. **Climate/fuel features are year-matched but gappy.** PRISM, USDM, LANDFIRE (LF2016+)
   and NLCD (2001-2021) connectors are implemented against verified endpoints, yet fuels
   before 2016 and land cover before 2001 reuse the earliest available version (the lag is
   a feature, not hidden), drought is null before 2000, and fire-weather events (Santa Ana
   and Diablo winds) are still absent. The model runs without any of them and records
   which were present.
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
- Manual LANDFIRE LF2001-2014 downloads to remove the pre-2016 fuel anachronism; fire
  weather (wind-event) features from NOAA/RAWS.
- NHD flowline distance and HAND-based pluvial proxies for flood.
- Alquist-Priolo fault zones, CGS Vs30 site amplification and quadrangle coverage polygons.
- County assessor parcel attributes (year built, construction) to replace NSI modelled
  values where a county publishes them; verification of the transcribed Hazus tables.
- Vector tiles (PMTiles) for hazard layers instead of per-request GeoJSON.
- Optuna tuning, LightGBM, conformal prediction intervals, MLflow tracking in CI.
- Correlated-event portfolio scenarios (e.g. a single perimeter footprint hitting many
  locations) instead of independent per-property sums.

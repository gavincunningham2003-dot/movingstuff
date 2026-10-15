# Known limitations and future improvements

## Limitations

1. **One machine, one sample.** Everything has been run end-to-end on a single workstation
   against a 14,994-address sample (see `PROGRESS.md` for every executed step and its real
   result). The sample is too small for the modern-era (2016+) fuel-feature model, which is
   recorded as insufficient data rather than reported.
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
13. **Historical stress tests replay footprints, not losses.** A CAL FIRE perimeter says
    which exposures the fire reached; structure survival inside perimeters varies from a few
    per cent to near-total between fires, so the damage ratio is an explicit input and the
    totals carry no frequency, no correlation and no expected annual loss. Perimeters are
    only available for wildfire; earthquake and flood footprints are not replayed.
14. **Financial logic is policy arithmetic, not pricing.** Gross loss is the replacement
    value times an assumed ratio; net applies the deductible and the limit (sum insured by
    default). There is no reinsurance, no sub-limits, no coinsurance, no loss adjustment
    expense and no inflation of values to the valuation date.
15. **Duplicate handling is geometric.** Exact duplicates need the same id at the same
    location (5 m); two policies with different ids at one site are kept as separate
    exposures and reported as accumulation, which is right for condominiums and wrong for a
    re-keyed resubmission with a new id.
16. **The CGS tsunami layer includes "outside hazard area" polygons.** They were stored on
    first ingestion and made inland points look exposed (found 2026-10-15, fixed in the
    module, the ingestion filter and the stored data); portfolio analyses created before
    the fix keep the inflated tsunami count and composite scores until re-uploaded.

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

# Screenshots

Captured from the running stack (14,994-address sample, live model
`wf-chrono-202610142205`) at 1440 px width with
`SCREENSHOTS=1 PLAYWRIGHT_CHANNEL=chrome npx playwright test screenshots` from
`frontend/` (see `frontend/e2e/screenshots.spec.ts`). The app scrolls inside its main panel, so
the `*-analysis` / `*-detail` captures show the top of a page and the named captures show the
block scrolled into view. Re-run the command after a re-ingest or retrain to refresh them.

| Page | File | What to look for |
|---|---|---|
| Executive overview | [overview.png](overview.png) | portfolio-wide counts, hazard distribution, exposure value, model summary |
| California hazard map | [hazard-map.png](hazard-map.png) | clustered addresses, FHSZ / flood / tsunami / perimeter overlays, click-anywhere assessment |
| Property risk explorer | [property-detail.png](property-detail.png), [property-prediction-inputs.png](property-prediction-inputs.png) | hazard profile with coverage status, composite breakdown, ML prediction with SHAP and the **prediction inputs** block (completeness, data freshness, top factors), NSI-driven financial scenarios |
| Model analytics | [model-analytics.png](model-analytics.png) | run selector, chronological-CV selection, calibration, operating points, spatial holdout, caveats |
| Portfolio analysis | [portfolio-analysis.png](portfolio-analysis.png), [portfolio-accumulation.png](portfolio-accumulation.png), [portfolio-stress-test.png](portfolio-stress-test.png), [portfolio-financial.png](portfolio-financial.png) | contract-driven column mapping, validation tiles (exact duplicates vs shared locations), location accumulation, **historical stress test** (Camp Fire replay), financial totals |
| Data quality and sources | [data-quality.png](data-quality.png) | provenance, versions, ingestion state, coverage, processing errors, planned/superseded sources |

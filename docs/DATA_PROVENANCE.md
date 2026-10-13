# Data provenance

The single source of truth is [`data_sources.yaml`](../data_sources.yaml). Each entry
records provider, URL/API, format, CRS, geographic and temporal coverage, licence,
authentication, the fields the connector relies on, the date it was verified and a
`verification_status`:

- **verified** — reached from the development environment on the recorded date, fields
  confirmed from service metadata.
- **unverified** — documented by the provider but not reachable from the development
  network (reason in `notes`); connector follows the published schema and validates on
  first run.
- **local_only** — no stable programmatic download; user places files in `data/raw/<id>/`.

Ingestion state (`retrieved_at`, `record_count`, `version`, `checksum`, errors) is kept in
the `hazard_sources` table and shown on the Data Quality page and `/api/v1/data-sources`.

## Verification log (2026-10-13)

| id | Check performed | Outcome |
|---|---|---|
| openaddresses_ca | `GET /api/data?source=us/ca&layer=addresses`; `GET /api/job/926561/output/source.geojson.gz` | listing OK (58 county sources); download 403 without token |
| census_tiger_counties | HEAD tl_2023_us_county.zip | 200, 83,451,409 bytes |
| ms_building_footprints_ca | HEAD California.geojson.zip (minedbuildings host) | 200, 466,385,561 bytes |
| calfire_fire_perimeters | layer metadata; `returnCountOnly` | 22 fields; 23,334 features (4,580 with YEAR_ ≥ 2015) |
| calfire_fhsz_2024 | layer metadata; point query (-120.65, 38.75) | fields FHSZ, FHSZ_Description, FHSZ_7Class, SRA22_2; feature returned |
| calfire_fhsz_sra_2023 | layer metadata; point query | fields SRA, FHSZ, FHSZ_Description |
| cgs_ms48_pga_2pc50 | ImageServer metadata; `getSamples`/`identify` | F32, 100 m, value 1.2534 g at (-122.18, 37.85); NoData at SF downtown |
| cgs_landslide_susceptibility_ms58 | MapServer `identify` | class 7 at (-122.18, 37.85); class 0 at (-122.4194, 37.7749) |
| cgs_qfaults | layer metadata | polyline, 13 fields |
| cgs_tsunami_hazard_area | layer metadata | polygon, fields Evacuate/County/Label |
| usgs_designmaps_asce7_16 | GET calculate | pgauh 0.772, pga 0.573, ss 1.5 at SF; model v4.0.x |
| usgs_3dep_1as | HEAD USGS_1_n38w123.tif | 200, 28,522,813 bytes |
| usgs_epqs | GET | value 16.51 m at SF |
| usgs_earthquake_catalog | FDSN count | 2,847 M4+ events since 1980 |
| fema_nfhl, fema_nri_counties | GET metadata / HEAD zip | connection reset by host (3 attempts each) |
| usace_nsi | `GET /nsiapi/structures?fips=06003` | GeoJSON with bldgtype, occtype, num_story, found_type, found_ht, ground_elv, med_yr_blt, val_struct, firmzone, static_bfe; `?bbox=` form returned an empty collection |
| landfire_fuels | GPServer metadata; `/api/products` | Layer_List / Area_of_Interest parameters confirmed; FBFM40/CC/EVT products listed for LF2016, LF2022-LF2025 only |
| prism_climate | HEAD `/prism/data/get/us/4km/ppt/2020`, `tmax/1995`, normals | application/zip (prism_ppt_us_25m_2020.zip etc.); old `/data/public/` URLs return a notice; TLS failed only under the dev network's certificate interception |
| us_drought_monitor | GET county statistics for 06001 | CSV with weekly None/D0-D4 area percentages |
| mrlc_nlcd | WCS GetCapabilities / DescribeCoverage | 597 coverages; NLCD_2001...2021_Land_Cover_L48 in EPSG:5070 with X/Y axes |
| mtbs_burn_severity | GET direct-download page | JS app with antibot form |
| landfire_fuels, mrlc_nlcd, cgs_seismic_hazard_zones, noaa_*, us_drought_monitor, census_acs_housing | not probed | documented endpoints only |

## Licences

| Dataset | Licence |
|---|---|
| OpenAddresses | per-source; mostly public domain / CC-BY / ODbL (see each `sources/us/ca/*.json`) |
| TIGER/Line, USGS, NOAA, FEMA, Census | US Government public domain |
| Microsoft Building Footprints | ODbL |
| USACE National Structure Inventory | US Government public domain |
| PRISM Climate Group | free for non-commercial use with attribution |
| US Drought Monitor (NDMC) | public |
| LANDFIRE, MRLC NLCD | US Government public domain |
| CAL FIRE FRAP / FHSZ | CAL FIRE open data terms |
| CGS layers | California Department of Conservation terms |

Raw data is never committed. Each download keeps a `.meta.json` with the URL, retrieval
timestamp, size and sha256.

## Known data caveats

- **Address points are not buildings.** OpenAddresses points are often parcel centroids;
  building association is a heuristic with an explicit uncertainty class.
- **Fire perimeters are incomplete** for small fires and earlier decades; absence is not
  evidence of no fire.
- **FHSZ is a present-day regulatory product**; it is reported but never used as an ML
  predictor for historical years.
- **NFHL has gaps**: unmapped areas are `outside_coverage`, not low risk.
- **CGS Seismic Hazard Zones** exist only for evaluated quadrangles.
- **NRI is county-level** and used only as regional context.
- **Census ACS medians are tract-level**; they are never used as a property's value.

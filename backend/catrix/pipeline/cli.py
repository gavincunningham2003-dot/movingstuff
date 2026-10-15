"""CATRIX command-line pipeline.

Examples::

    catrix db upgrade                    # alembic upgrade head
    catrix ingest counties               # TIGER counties
    catrix ingest addresses --sample 15000
    catrix ingest perimeters --min-year 1950
    catrix ingest layer wildfire_fhsz    # any key in polygon_layers.LAYERS
    catrix ingest faults | events | nri | buildings | elevation | environment
    catrix ingest all                    # the full Milestone 1-2 chain
    catrix assess                        # run every hazard module over all addresses
    catrix score                         # composite index
    catrix ml build-dataset | train | predict
    catrix quality                       # data-quality checks
    catrix demo                          # enrich the bundled example portfolio, print the summary
    catrix api                           # uvicorn
"""

from __future__ import annotations

import subprocess
import sys
from typing import Annotated

import typer

from catrix.config import get_settings
from catrix.logging import configure_logging, get_logger

app = typer.Typer(help="CATRIX pipeline", no_args_is_help=True)
ingest_app = typer.Typer(help="Dataset connectors", no_args_is_help=True)
ml_app = typer.Typer(help="Wildfire ML experiment", no_args_is_help=True)
db_app = typer.Typer(help="Database", no_args_is_help=True)
app.add_typer(ingest_app, name="ingest")
app.add_typer(ml_app, name="ml")
app.add_typer(db_app, name="db")
log = get_logger(__name__)


@app.callback()
def _init() -> None:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    s.ensure_dirs()


def _run_connector(connector, **kwargs) -> None:
    from catrix.db.session import session_scope
    from catrix.ingest.registry import sync_registry

    with session_scope() as session:
        sync_registry(session)
        res = connector.run(session, **kwargs)
    typer.echo(
        f"{res.source_id}: {res.status} records={res.record_count} version={res.version} {res.message or ''}"
    )
    if res.status in ("failed",):
        raise typer.Exit(code=1)


@db_app.command("upgrade")
def db_upgrade() -> None:
    raise typer.Exit(subprocess.call([sys.executable, "-m", "alembic", "upgrade", "head"]))


@ingest_app.command("counties")
def ingest_counties(force: bool = False) -> None:
    from catrix.ingest.counties import CountiesConnector

    _run_connector(CountiesConnector(), force=force)


@ingest_app.command("addresses")
def ingest_addresses(
    sample: Annotated[int | None, typer.Option(help="number of address points")] = None,
    county: Annotated[
        list[str] | None, typer.Option(help="OpenAddresses source ids, e.g. us/ca/alameda")
    ] = None,
    force: bool = False,
) -> None:
    from catrix.ingest.openaddresses import OpenAddressesConnector

    _run_connector(OpenAddressesConnector(), force=force, counties=county, sample_size=sample)


@ingest_app.command("perimeters")
def ingest_perimeters(min_year: int = 1950, force: bool = False) -> None:
    from catrix.ingest.calfire import CalFirePerimetersConnector

    _run_connector(CalFirePerimetersConnector(), force=force, min_year=min_year)


@ingest_app.command("layer")
def ingest_layer(
    key: str,
    bbox: Annotated[
        str | None,
        typer.Option(
            help="minLon,minLat,maxLon,maxLat; defaults to the bbox of ingested addresses"
        ),
    ] = None,
    force: bool = False,
) -> None:
    from catrix.ingest.polygon_layers import LAYERS, PolygonLayerConnector

    if key not in LAYERS:
        typer.echo(f"unknown layer {key}; choose from {sorted(LAYERS)}")
        raise typer.Exit(2)
    bb = tuple(float(v) for v in bbox.split(",")) if bbox else _address_bbox()
    _run_connector(PolygonLayerConnector(key), force=force, bbox=bb)


def _address_bbox() -> tuple[float, float, float, float] | None:
    from sqlalchemy import func, select

    from catrix.db.models import Address
    from catrix.db.session import session_scope

    with session_scope() as s:
        row = s.execute(
            select(
                func.min(Address.lon),
                func.min(Address.lat),
                func.max(Address.lon),
                func.max(Address.lat),
            )
        ).one()
    if row[0] is None:
        return None
    pad = 0.05
    return (row[0] - pad, row[1] - pad, row[2] + pad, row[3] + pad)


@ingest_app.command("faults")
def ingest_faults(force: bool = False) -> None:
    from catrix.ingest.lines_and_events import FaultsConnector

    _run_connector(FaultsConnector(), force=force)


@ingest_app.command("events")
def ingest_events(force: bool = False) -> None:
    from catrix.ingest.lines_and_events import EarthquakeEventsConnector

    _run_connector(EarthquakeEventsConnector(), force=force)


@ingest_app.command("nri")
def ingest_nri(force: bool = False) -> None:
    from catrix.ingest.nri import NRIConnector

    _run_connector(NRIConnector(), force=force)


@ingest_app.command("buildings")
def ingest_buildings(force: bool = False) -> None:
    from catrix.ingest.buildings import BuildingsConnector

    _run_connector(BuildingsConnector(), force=force)


@ingest_app.command("elevation")
def ingest_elevation(force: bool = False, max_tiles: int = 40) -> None:
    from catrix.ingest.elevation import ElevationConnector

    _run_connector(ElevationConnector(), force=force, max_tiles=max_tiles)


@ingest_app.command("environment")
def ingest_environment() -> None:
    from catrix.ingest.environment import EnvironmentConnector

    _run_connector(EnvironmentConnector())


@ingest_app.command("nsi")
def ingest_nsi(force: bool = False, include_large: bool = False) -> None:
    """USACE National Structure Inventory (building attributes for Hazus vulnerability)."""
    from catrix.ingest.nsi import NSIConnector

    _run_connector(NSIConnector(), force=force, include_large=include_large)


@ingest_app.command("prism")
def ingest_prism(force: bool = False, start_year: int = 1991, end_year: int | None = None) -> None:
    """PRISM annual precipitation / Tmax per address (ML climate features)."""
    from catrix.ingest.prism import PRISMConnector

    _run_connector(PRISMConnector(), force=force, start_year=start_year, end_year=end_year)


@ingest_app.command("drought")
def ingest_drought(force: bool = False) -> None:
    """US Drought Monitor county statistics (ML drought features)."""
    from catrix.ingest.drought import DroughtConnector

    _run_connector(DroughtConnector(), force=force)


@ingest_app.command("landfire")
def ingest_landfire(
    force: bool = False,
    version: Annotated[list[str] | None, typer.Option(help="LF versions, e.g. LF2016")] = None,
) -> None:
    """LANDFIRE fuel model / canopy cover clips via LFPS (ML fuel features)."""
    from catrix.ingest.landfire import LandfireConnector

    _run_connector(LandfireConnector(), force=force, versions=version)


@ingest_app.command("nlcd")
def ingest_nlcd(
    force: bool = False,
    epoch: Annotated[list[int] | None, typer.Option(help="NLCD epochs, e.g. 2016")] = None,
) -> None:
    """MRLC NLCD land cover epochs via WCS (ML land-cover features)."""
    from catrix.ingest.nlcd import NLCDConnector

    _run_connector(NLCDConnector(), force=force, epochs=epoch)


@ingest_app.command("features")
def ingest_features() -> None:
    """Year-matched ML feature sources: PRISM, USDM, LANDFIRE, NLCD (each tolerant of failure)."""
    from catrix.ingest.drought import DroughtConnector
    from catrix.ingest.landfire import LandfireConnector
    from catrix.ingest.nlcd import NLCDConnector
    from catrix.ingest.prism import PRISMConnector

    for c in (PRISMConnector(), DroughtConnector(), LandfireConnector(), NLCDConnector()):
        try:
            _run_connector(c)
        except typer.Exit:
            typer.echo(f"{c.source_id} failed (continuing)")


@ingest_app.command("all")
def ingest_all(
    sample: int | None = None, min_year: int = 1950, skip_buildings: bool = False
) -> None:
    """Milestone 1-2 chain. Failures of optional sources are reported, not fatal."""
    from catrix.ingest.buildings import BuildingsConnector
    from catrix.ingest.calfire import CalFirePerimetersConnector
    from catrix.ingest.counties import CountiesConnector
    from catrix.ingest.elevation import ElevationConnector
    from catrix.ingest.environment import EnvironmentConnector
    from catrix.ingest.lines_and_events import EarthquakeEventsConnector, FaultsConnector
    from catrix.ingest.nri import NRIConnector
    from catrix.ingest.openaddresses import OpenAddressesConnector
    from catrix.ingest.polygon_layers import PolygonLayerConnector

    _run_connector(CountiesConnector())
    _run_connector(OpenAddressesConnector(), sample_size=sample)
    _run_connector(CalFirePerimetersConnector(), min_year=min_year)
    bb = _address_bbox()
    for key in ("wildfire_fhsz", "tsunami", "flood_nfhl", "liquefaction"):
        try:
            _run_connector(PolygonLayerConnector(key), bbox=bb)
        except typer.Exit:
            typer.echo(f"layer {key} failed (continuing)")
    for c in (FaultsConnector(), EarthquakeEventsConnector(), NRIConnector()):
        try:
            _run_connector(c)
        except typer.Exit:
            typer.echo(f"{c.source_id} failed (continuing)")
    if not skip_buildings:
        try:
            _run_connector(BuildingsConnector())
        except typer.Exit:
            typer.echo("buildings failed (continuing)")
    _run_connector(ElevationConnector())
    from catrix.ingest.nsi import NSIConnector

    try:
        _run_connector(NSIConnector())
    except typer.Exit:
        typer.echo("nsi failed (continuing)")
    ingest_features()
    _run_connector(EnvironmentConnector())


@app.command("assess")
def assess(
    hazard: Annotated[list[str] | None, typer.Option(help="limit to hazard ids")] = None,
    batch_size: int | None = None,
) -> None:
    from catrix.db.session import session_scope
    from catrix.hazards.registry import assess_all_addresses

    with session_scope() as s:
        counts = assess_all_addresses(s, hazard_ids=hazard, batch_size=batch_size)
    for k, v in counts.items():
        typer.echo(f"{k}: {v}")


@app.command("score")
def score() -> None:
    from catrix.db.session import session_scope
    from catrix.scoring.persist import score_all_addresses

    with session_scope() as s:
        n = score_all_addresses(s)
    typer.echo(f"composite scores: {n}")


@ml_app.command("build-dataset")
def ml_build_dataset(
    target: str = typer.Option(
        "inside", help="label definition: inside | within_1km | modern (2016+ experiment)"
    ),
) -> None:
    from catrix.db.session import session_scope
    from catrix.ml.dataset import SPECS, build_dataset

    if target not in SPECS:
        typer.echo(f"unknown target {target!r}; choose from {sorted(SPECS)}")
        raise typer.Exit(2)
    with session_scope() as s:
        df = build_dataset(s, SPECS[target])
    typer.echo(
        f"rows={len(df)} positives={int(df['label'].sum())} prevalence={df['label'].mean():.5f}"
    )
    typer.echo(df["split"].value_counts().to_string())


@ml_app.command("train")
def ml_train(
    version: str | None = None,
    target: str = typer.Option(
        "inside", help="dataset target to train on: inside | within_1km | modern"
    ),
    no_lag_features: bool = typer.Option(
        False,
        "--no-lag-features",
        help="drop the *_version_lag_years columns (calendar-year proxies)",
    ),
    no_select: bool = typer.Option(
        False, "--no-select", help="record the run without making it the model the API uses"
    ),
    features: str = typer.Option(
        "default", help="feature policy: default (mandatory + covered optional) | all | mandatory"
    ),
    min_coverage: float = typer.Option(
        0.5, help="minimum non-null coverage for an optional/experimental feature to be used"
    ),
) -> None:
    from catrix.db.session import session_scope
    from catrix.ml.dataset import SPECS, load_dataset
    from catrix.ml.train import train_all

    if target not in SPECS:
        typer.echo(f"unknown target {target!r}; choose from {sorted(SPECS)}")
        raise typer.Exit(2)
    spec = SPECS[target]
    df = load_dataset(spec.version)
    if df is None:
        typer.echo(
            f"dataset {spec.version} not found; run `catrix ml build-dataset --target {target}`"
        )
        raise typer.Exit(1)
    exclude = [c for c in df.columns if c.endswith("_version_lag_years")] if no_lag_features else []
    with session_scope() as s:
        doc = train_all(
            df,
            spec,
            session=s,
            version=version,
            exclude_features=exclude,
            set_latest=not no_select,
            feature_policy=features,
            min_coverage=min_coverage,
        )
    typer.echo(
        f"status={doc['status']} selected={doc.get('selected_model')} version={doc.get('version')}"
    )
    for m in doc.get("models", []):
        t = m["test"]["metrics"]
        typer.echo(
            f"  {m['name']:<32} ROC-AUC={t['roc_auc']} PR-AUC={t['pr_auc']} Brier={t['brier']:.5f} F1={t['f1']:.3f}"
        )
    if doc.get("status_detail"):
        typer.echo(doc["status_detail"])


@ml_app.command("predict")
def ml_predict(feature_year: int | None = None) -> None:
    from catrix.db.session import session_scope
    from catrix.ml.predict import predict_all_addresses, reset_model_cache

    reset_model_cache()
    with session_scope() as s:
        n = predict_all_addresses(s, feature_year=feature_year)
    typer.echo(f"predictions: {n}")


@app.command("demo")
def demo(
    csv: Annotated[
        str | None, typer.Option(help="portfolio CSV (default: bundled example)")
    ] = None,
    name: str = "demo",
    network: Annotated[bool, typer.Option(help="allow point-service / raster lookups")] = False,
) -> None:
    """Upload and enrich the example portfolio, then print the underwriter's first look
    (counts, money totals, top exposures, location accumulation, historical fires)."""
    from pathlib import Path

    from catrix.db.session import session_scope
    from catrix.pipeline.demo import format_report, run_demo

    with session_scope() as s:
        report = run_demo(s, csv_path=Path(csv) if csv else None, name=name, allow_network=network)
    typer.echo(format_report(report))
    if report["status"] != "completed":
        raise typer.Exit(code=1)


@app.command("quality")
def quality() -> None:
    from catrix.db.session import session_scope
    from catrix.quality.checks import persist, run_checks

    with session_scope() as s:
        res = run_checks(s)
        persist(s, "manual", res)
    for r in res:
        typer.echo(f"{r.status:<8} {r.name:<45} checked={r.n_checked} failed={r.n_failed}")


@app.command("api")
def api(host: str = "0.0.0.0", port: int = 8000, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run("catrix.api.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()

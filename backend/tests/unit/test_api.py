"""API tests without a database: schema validation, health degradation, guard rails."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from catrix.api.deps import db_session, parse_bbox
from catrix.api.main import create_app
from catrix.api.schemas import HazardAssessmentOut, PropertyDetailOut


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CATRIX_DATABASE_URL", "postgresql+psycopg://nobody:nobody@127.0.0.1:1/nope")
    from catrix.config import get_settings
    from catrix.db.session import reset_engine

    get_settings.cache_clear()
    reset_engine()
    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


def test_health_reports_degraded_without_db(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded" and body["database"] == "unavailable" and body["version"]


def test_hazard_catalogue_needs_no_db(client):
    r = client.get("/api/v1/hazards")
    assert r.status_code == 200
    ids = {h["hazard_id"] for h in r.json()}
    assert {"wildfire_fhsz", "earthquake_pga", "flood_nfhl"} <= ids
    eq = next(h for h in r.json() if h["hazard_id"] == "earthquake_pga")
    assert eq["probability_horizon"] == "2% in 50 years" and eq["units"] == "g"


def test_models_metrics_not_trained(client):
    app = client.app

    class _Fake:
        def execute(self, *_a, **_k):
            class R:
                def first(self):
                    return None

            return R()

        def close(self):
            pass

    app.dependency_overrides[db_session] = lambda: _Fake()
    r = client.get("/api/v1/models/metrics")
    assert r.status_code == 200 and r.json()["status"] == "not_trained"


def test_bbox_guard_rails():
    assert parse_bbox(None) is None
    assert parse_bbox("-122.5,37.5,-122.0,38.0") == (-122.5, 37.5, -122.0, 38.0)
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        parse_bbox("-125,32,-114,42")  # whole state: too large
    with pytest.raises(HTTPException):
        parse_bbox("a,b,c,d")


def test_openapi_lists_versioned_routes(client):
    paths = client.get("/api/openapi.json").json()["paths"]
    for p in (
        "/api/v1/health",
        "/api/v1/data-sources",
        "/api/v1/properties",
        "/api/v1/properties/{property_id}",
        "/api/v1/properties/{property_id}/hazards",
        "/api/v1/properties/{property_id}/predictions",
        "/api/v1/hazards",
        "/api/v1/portfolio/summary",
        "/api/v1/portfolio/upload",
        "/api/v1/portfolio/{portfolio_id}/analysis",
        "/api/v1/models/metrics",
        "/api/v1/data-quality",
    ):
        assert p in paths, p


def test_prediction_schema_rejects_bad_payloads():
    ok = HazardAssessmentOut(
        hazard_id="x", hazard_name="x", source_id="s", coverage_status="covered", value=1.0
    )
    assert ok.coverage_status == "covered"
    with pytest.raises(ValueError):
        HazardAssessmentOut(hazard_id="x", hazard_name="x", source_id="s", coverage_status="maybe")
    d = PropertyDetailOut(
        id=1, lat=37.0, lon=-122.0, source_id="openaddresses_ca", acquired_at=datetime.now(UTC)
    )
    assert d.hazards == [] and d.prediction is None

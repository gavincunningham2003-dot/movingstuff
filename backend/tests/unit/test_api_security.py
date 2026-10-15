"""API-key protection (no database needed: the key check runs before any query)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from catrix.config import get_settings


def _app(monkeypatch, key: str | None):
    if key is None:
        monkeypatch.delenv("CATRIX_API_KEY", raising=False)
    else:
        monkeypatch.setenv("CATRIX_API_KEY", key)
    get_settings.cache_clear()
    from catrix.api.main import create_app

    return TestClient(create_app())


def test_routes_are_open_when_no_key_is_configured(monkeypatch):
    client = _app(monkeypatch, None)
    # an invalid job id fails validation (422), proving the key check did not reject it
    assert client.get("/api/v1/jobs/not-a-uuid").status_code == 422
    get_settings.cache_clear()


def test_key_required_on_protected_routes_but_not_health(monkeypatch):
    client = _app(monkeypatch, "s3cret")
    assert client.get("/api/v1/health").status_code == 200
    r = client.get("/api/v1/jobs/not-a-uuid")
    assert r.status_code == 401 and "X-API-Key" in r.json()["detail"]
    assert client.get("/api/v1/jobs/not-a-uuid", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/v1/jobs/not-a-uuid", headers={"X-API-Key": "s3cret"}).status_code == 422
    assert client.post("/api/v1/portfolio/upload").status_code == 401
    get_settings.cache_clear()


def test_allowed_origins_setting_drives_cors(monkeypatch):
    monkeypatch.setenv("CATRIX_ALLOWED_ORIGINS", '["https://risk.example.com"]')
    client = _app(monkeypatch, None)
    r = client.options(
        "/api/v1/health",
        headers={"Origin": "https://risk.example.com", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") == "https://risk.example.com"
    r = client.options(
        "/api/v1/health",
        headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") is None
    get_settings.cache_clear()

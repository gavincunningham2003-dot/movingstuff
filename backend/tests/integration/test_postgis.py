"""PostGIS integration tests. Require ``CATRIX_TEST_DATABASE_URL`` pointing at a PostGIS
database that may be dropped and recreated (e.g. the docker-compose ``db`` service with a
``catrix_test`` database). Everything inserted here is SYNTHETIC and labelled."""

from __future__ import annotations

import uuid

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from catrix.db.models import (
    Address,
    Base,
    HazardGeometry,
    HazardLine,
    HazardSource,
    PropertyHazardAssessment,
    Region,
    WildfireEvent,
)
from tests.conftest import integration_db_url, requires_db

pytestmark = [pytest.mark.integration, requires_db]


@pytest.fixture(scope="module")
def engine():
    from catrix.db.session import get_engine, reset_engine

    reset_engine()
    eng = get_engine(integration_db_url())
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        Base.metadata.drop_all(conn)
        Base.metadata.create_all(conn)
    yield eng
    reset_engine()


@pytest.fixture(scope="module")
def seeded(engine):
    from catrix.db.session import get_sessionmaker

    s = get_sessionmaker()()
    s.add(HazardSource(id="openaddresses_ca", name="oa", ingestion_status="ingested"))
    s.add(
        HazardSource(
            id="calfire_fhsz_2024", name="fhsz", ingestion_status="ingested", version="test"
        )
    )
    s.add(
        HazardSource(
            id="calfire_fire_perimeters", name="perims", ingestion_status="ingested", version="test"
        )
    )
    s.add(
        HazardSource(id="cgs_qfaults", name="faults", ingestion_status="ingested", version="test")
    )
    s.add(HazardSource(id="fema_nfhl", name="nfhl", ingestion_status="ingested", version="test"))
    s.add(
        Region(
            region_type="county",
            fips="06001",
            name="Alameda",
            geom="SRID=4326;MULTIPOLYGON(((-122.4 37.6,-121.6 37.6,-121.6 38.0,-122.4 38.0,-122.4 37.6)))",
        )
    )
    s.flush()
    s.add(
        Address(
            id=1,
            source_id="openaddresses_ca",
            number="1",
            street="SYNTHETIC ST",
            city="BERKELEY",
            lat=37.87,
            lon=-122.27,
            county_fips="06001",
            geom="SRID=4326;POINT(-122.27 37.87)",
            dedup_key="k1",
            hash="h1",
        )
    )
    s.add(
        Address(
            id=2,
            source_id="openaddresses_ca",
            number="2",
            street="SYNTHETIC AVE",
            city="OAKLAND",
            lat=37.80,
            lon=-122.25,
            county_fips="06001",
            geom="SRID=4326;POINT(-122.25 37.80)",
            dedup_key="k2",
            hash="h2",
        )
    )
    s.add(
        HazardGeometry(
            source_id="calfire_fhsz_2024",
            hazard_id="wildfire_fhsz",
            classification="Very High",
            class_code=3,
            attributes={"SRA22_2": "SRA"},
            geom="SRID=4326;MULTIPOLYGON(((-122.3 37.85,-122.2 37.85,-122.2 37.9,-122.3 37.9,-122.3 37.85)))",
        )
    )
    s.add(
        WildfireEvent(
            source_id="calfire_fire_perimeters",
            source_object_id=1,
            year=2020,
            fire_name="SYNTHETIC FIRE",
            gis_acres=500.0,
            geom="SRID=4326;MULTIPOLYGON(((-122.29 37.86,-122.26 37.86,-122.26 37.88,-122.29 37.88,-122.29 37.86)))",
        )
    )
    s.add(
        WildfireEvent(
            source_id="calfire_fire_perimeters",
            source_object_id=2,
            year=1991,
            fire_name="OLD SYNTHETIC FIRE",
            gis_acres=50.0,
            geom="SRID=4326;MULTIPOLYGON(((-122.27 37.79,-122.24 37.79,-122.24 37.83,-122.27 37.83,-122.27 37.79)))",
        )
    )
    s.add(
        HazardGeometry(
            source_id="fema_nfhl",
            hazard_id="flood_nfhl",
            classification="AE",
            attributes={"FLD_ZONE": "AE", "ZONE_SUBTY": None, "SFHA_TF": "T", "STATIC_BFE": 12.0},
            geom="SRID=4326;MULTIPOLYGON(((-122.28 37.86,-122.26 37.86,-122.26 37.88,-122.28 37.88,-122.28 37.86)))",
        )
    )
    s.add(
        HazardLine(
            source_id="cgs_qfaults",
            hazard_id="fault",
            name="SYNTHETIC FAULT",
            attributes={},
            geom="SRID=4326;MULTILINESTRING((-122.30 37.80,-122.30 37.90))",
        )
    )
    s.commit()
    yield s
    s.close()


def test_polygon_lookup_and_fire_history(seeded):
    from catrix.hazards.lookup import fire_history_lookup, polygon_lookup

    pts = pd.DataFrame({"lon": [-122.27, -122.25], "lat": [37.87, 37.80]}, index=[1, 2])
    hits = polygon_lookup(seeded, "wildfire_fhsz", pts)
    assert hits[1]["classification"] == "Very High" and 2 not in hits
    fh = fire_history_lookup(seeded, pts, since_year=1990, radius_m=10_000)
    assert fh[1]["inside_any"] is True and fh[1]["last_inside_year"] == 2020
    # the 1991 fire covers point 2; the 2020 fire is ~6.7 km north of it (inside 10 km)
    assert fh[2]["n_fires_radius"] == 2 and fh[2]["inside_any"] is True
    limited = fire_history_lookup(seeded, pts, since_year=1990, radius_m=5000, until_year=2019)
    assert limited[1]["inside_any"] is False  # 2020 perimeter excluded: no leakage


def test_assess_and_score_all_addresses(seeded):
    from catrix.hazards.registry import assess_all_addresses
    from catrix.scoring.persist import score_all_addresses

    # wildfire + earthquake families cover 65 % of the index weight, enough for a score;
    # tsunami is deliberately not ingested and must be reported as unavailable.
    counts = assess_all_addresses(
        seeded,
        hazard_ids=[
            "wildfire_fhsz",
            "wildfire_history",
            "earthquake_fault_proximity",
            "flood_nfhl",
            "tsunami",
        ],
        batch_size=1,
    )
    # wildfire (0.35) + flood (0.20) families cover 55 % of the index weight, enough for a
    # score without any network lookups; tsunami is deliberately not ingested.
    assert counts == {
        "wildfire_fhsz": 2,
        "wildfire_history": 2,
        "earthquake_fault_proximity": 2,
        "flood_nfhl": 2,
        "tsunami": 2,
    }
    rows = seeded.execute(
        text(
            "SELECT hazard_id, coverage_status FROM property_hazard_assessments WHERE address_id = 1 ORDER BY 1"
        )
    ).all()
    d = dict(rows)
    assert (
        d["wildfire_fhsz"] == "covered"
        and d["wildfire_history"] == "covered"
        and d["tsunami"] == "unavailable"
    )
    assert score_all_addresses(seeded) == 2
    sc = seeded.execute(
        text(
            "SELECT value FROM property_hazard_assessments WHERE hazard_id='_composite' AND address_id=1"
        )
    ).scalar()
    assert sc is not None and 0 <= sc <= 100


def test_api_against_postgis(seeded, monkeypatch):
    monkeypatch.setenv("CATRIX_DATABASE_URL", integration_db_url())
    from catrix.api.main import create_app
    from catrix.config import get_settings

    get_settings.cache_clear()
    client = TestClient(create_app())
    r = client.get("/api/v1/properties", params={"q": "SYNTHETIC ST"})
    assert r.status_code == 200 and r.json()["total"] == 1
    pid = r.json()["items"][0]["id"]
    detail = client.get(f"/api/v1/properties/{pid}").json()
    assert detail["wildfire_fhsz"] == "Very High"
    hz = {h["hazard_id"]: h for h in detail["hazards"]}
    assert (
        hz["wildfire_fhsz"]["source_id"] == "calfire_fhsz_2024"
        and hz["wildfire_history"]["details"]["inside_any_perimeter"] is True
    )
    assert (
        detail["score"]["score"] is not None
        and "assumption" in detail["score"]["weights_note"].lower()
    )
    gj = client.get(
        "/api/v1/hazards/wildfire_fhsz/geojson",
        params={"bbox": "-122.5,37.5,-122.0,38.0", "zoom": 10},
    ).json()
    assert gj["features"] and gj["legend"]
    summary = client.get("/api/v1/portfolio/summary").json()
    assert summary["total_properties"] == 2 and summary["counties"][0]["county"] == "Alameda"
    dq = client.get("/api/v1/data-quality").json()
    assert any(
        d["source_id"] == "calfire_fhsz_2024" and d["ingestion_status"] == "ingested"
        for d in dq["datasets"]
    )


def test_portfolio_upload_roundtrip(seeded, monkeypatch):
    monkeypatch.setenv("CATRIX_DATABASE_URL", integration_db_url())
    from catrix.api.main import create_app
    from catrix.config import get_settings
    from catrix.portfolio.enrich import run_portfolio_job

    get_settings.cache_clear()
    client = TestClient(create_app())
    csv = (
        b"property_id,latitude,longitude,property_value,insured_value,deductible\n"
        b"A,37.87,-122.27,1000000,1000000,5000\n"
        b"B,37.80,-122.25,,500000,\n"
        b"B,37.80,-122.25,,500000,\n"  # exact duplicate: excluded
        b"B2,37.80,-122.25,,500000,\n"  # second policy at the same site: kept
    )
    up = client.post(
        "/api/v1/portfolio/upload",
        files={"file": ("p.csv", csv, "text/csv")},
        data={"name": "synthetic"},
    )
    assert up.status_code == 202
    pid, jid = up.json()["portfolio_id"], up.json()["job_id"]
    import uuid

    run_portfolio_job(seeded, uuid.UUID(jid), allow_network=False)
    st = client.get(f"/api/v1/portfolio/{pid}").json()
    assert st["status"] == "completed" and st["n_duplicates"] == 1
    assert st["n_shared_location"] == 1 and st["n_locations"] == 2
    an = client.get(f"/api/v1/portfolio/{pid}/analysis").json()
    assert an["summary"]["n_properties"] == 3 and an["summary"]["n_fhsz_very_high"] == 1
    assert an["financial"]["total_insured_value"] == 2_000_000
    assert an["financial"]["total_replacement_value"] == 1_000_000
    assert an["location_accumulation"][0]["n_exposures"] == 2
    contract = client.get("/api/v1/portfolio/csv-contract").json()
    assert any(c["name"] == "limit" and not c["required"] for c in contract)
    wf = next(
        s
        for s in an["financial"]["scenarios"]
        if s["scenario_id"] == "wildfire_vhfhsz_structure_loss"
    )
    assert wf["gross_loss"] == 800_000
    csv_out = client.get(f"/api/v1/portfolio/{pid}/export", params={"format": "csv"}).text
    assert "wildfire_fhsz__class" in csv_out.splitlines()[0] and "Very High" in csv_out


def test_portfolio_stress_test_replays_a_perimeter(seeded, monkeypatch):
    """A (seeded) 2020 perimeter covers the Berkeley point; the 1991 one covers nothing."""
    monkeypatch.setenv("CATRIX_DATABASE_URL", integration_db_url())
    from catrix.api.main import create_app
    from catrix.config import get_settings
    from catrix.portfolio.enrich import run_portfolio_job

    get_settings.cache_clear()
    client = TestClient(create_app())
    csv = (
        b"property_id,latitude,longitude,property_value,insured_value,deductible\n"
        b"A,37.87,-122.27,1000000,1000000,5000\n"
        b"B,37.80,-122.25,,500000,\n"
        b"B2,37.80,-122.25,,500000,\n"
    )
    up = client.post(
        "/api/v1/portfolio/upload",
        files={"file": ("stress.csv", csv, "text/csv")},
        data={"name": "stress"},
    )
    pid, jid = up.json()["portfolio_id"], up.json()["job_id"]
    run_portfolio_job(seeded, uuid.UUID(jid), allow_network=False)
    lst = client.get(f"/api/v1/portfolio/{pid}/stress-tests").json()
    assert lst["n_exposures"] == 3 and lst["total_insured_value"] == 2_000_000
    ev = next(e for e in lst["events"] if e["fire_name"] == "SYNTHETIC FIRE")
    assert ev["n_exposures_inside"] == 1 and ev["total_insured_value_inside"] == 1_000_000
    assert ev["share_of_tiv_pct"] == 50.0
    # the 1991 perimeter contains the Oakland site: B (its exact duplicate is excluded) and B2
    old = next(e for e in lst["events"] if e["fire_name"] == "OLD SYNTHETIC FIRE")
    assert old["n_exposures_inside"] == 2 and old["total_insured_value_inside"] == 1_000_000
    det = client.get(
        f"/api/v1/portfolio/{pid}/stress-tests/{ev['event_id']}",
        params={"damage_ratio": 0.5},
    ).json()
    t = det["totals"]
    assert t["n_exposures_inside"] == 1 and t["share_of_tiv_pct"] == 50.0
    # A: replacement value 1,000,000 x 0.5 = 500,000 gross; deductible 5,000; limit = sum insured
    assert t["gross_loss"] == 500_000 and t["net_insured_loss"] == 495_000
    assert [h["property_id"] for h in det["hits"]] == ["A"]
    assert det["perimeter"]["type"] in ("Polygon", "MultiPolygon")
    # replaying the old fire with a 5 km buffer: B and B2 inside, A ~4.4 km away as a near miss
    det2 = client.get(
        f"/api/v1/portfolio/{pid}/stress-tests/{old['event_id']}",
        params={"damage_ratio": 0.5, "buffer_m": 5000},
    ).json()
    inside = [h for h in det2["hits"] if h["hit_kind"] == "inside"]
    near = [h for h in det2["hits"] if h["hit_kind"] == "within_buffer"]
    assert sorted(h["property_id"] for h in inside) == ["B", "B2"]
    assert [h["property_id"] for h in near] == ["A"] and near[0]["gross_loss"] is None
    assert 4_000 < near[0]["distance_m"] < 5_000
    # sum insured used as proxy value and as the limit: 500,000 x 0.5 = 250,000 each
    assert det2["totals"]["gross_loss"] == 500_000 and det2["totals"]["net_insured_loss"] == 500_000
    assert det2["totals"]["n_with_terms"] == 2 and det2["totals"]["n_exposures_within_buffer"] == 1
    csv_out = client.get(
        f"/api/v1/portfolio/{pid}/stress-tests/{ev['event_id']}/export",
        params={"damage_ratio": 0.5},
    ).text
    assert "fire_name" in csv_out.splitlines()[0] and "SYNTHETIC FIRE" in csv_out
    assert client.get(f"/api/v1/portfolio/{pid}/stress-tests/999999").status_code == 404


def test_assessment_refresh_is_versioned_and_atomic(seeded, monkeypatch):
    """A failed refresh must leave the previous assessment untouched."""
    from sqlalchemy import func, select

    from catrix.db.models import AssessmentRun, AssessmentStaging
    from catrix.hazards import registry
    from catrix.hazards.registry import assess_all_addresses

    live_before = seeded.execute(
        select(func.count()).select_from(PropertyHazardAssessment)
    ).scalar_one()
    runs = list(seeded.execute(select(AssessmentRun)).scalars())
    assert runs and any(r.status == "completed" and r.kind == "hazards" for r in runs)
    with_run = seeded.execute(
        select(func.count())
        .select_from(PropertyHazardAssessment)
        .where(PropertyHazardAssessment.run_id.is_not(None))
    ).scalar_one()
    assert with_run == live_before

    def boom(*_a, **_k):
        raise RuntimeError("simulated connector outage")

    monkeypatch.setattr(registry, "assess_points", boom)
    with pytest.raises(RuntimeError, match="simulated"):
        assess_all_addresses(seeded, hazard_ids=["wildfire_fhsz"])
    monkeypatch.undo()
    live_after = seeded.execute(
        select(func.count()).select_from(PropertyHazardAssessment)
    ).scalar_one()
    assert live_after == live_before
    latest = (
        seeded.execute(select(AssessmentRun).order_by(AssessmentRun.started_at.desc()))
        .scalars()
        .first()
    )
    assert latest is not None and latest.status == "failed" and "simulated" in (latest.error or "")
    assert seeded.execute(select(func.count()).select_from(AssessmentStaging)).scalar_one() == 0
    # a good refresh of one hazard replaces only that hazard's rows and stamps the run id
    counts = assess_all_addresses(seeded, hazard_ids=["wildfire_fhsz"])
    assert counts == {"wildfire_fhsz": 2}
    rows = list(
        seeded.execute(
            select(PropertyHazardAssessment).where(
                PropertyHazardAssessment.hazard_id == "wildfire_fhsz"
            )
        ).scalars()
    )
    newest = (
        seeded.execute(select(AssessmentRun).order_by(AssessmentRun.started_at.desc()))
        .scalars()
        .first()
    )
    assert newest is not None and newest.status == "completed"
    assert len(rows) == 2 and all(r.run_id == newest.id for r in rows)
    assert (
        seeded.execute(select(func.count()).select_from(PropertyHazardAssessment)).scalar_one()
        == live_before
    )


def test_job_leases_retries_and_recovery(seeded):
    from datetime import UTC, datetime, timedelta

    from catrix.db.models import ProcessingJob
    from catrix.portfolio.jobs import claim_job, claim_next, recover_stale_jobs

    job = ProcessingJob(job_type="portfolio_enrich", status="pending", params={}, max_attempts=2)
    seeded.add(job)
    seeded.commit()
    jid = job.id
    assert claim_job(seeded, jid, "w1") is True
    assert claim_job(seeded, jid, "w2") is False  # live lease held by w1
    seeded.refresh(job)
    assert job.status == "claimed" and job.attempts == 1 and job.worker_id == "w1"
    assert job.lease_expires_at is not None and job.lease_expires_at > datetime.now(UTC)
    # runner dies: lease expires, the sweep returns the job to the queue
    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    seeded.commit()
    assert recover_stale_jobs(seeded) == {"retried": 1, "failed": 0}
    seeded.refresh(job)
    assert job.status == "pending" and "lease expired" in (job.last_error or "")
    assert claim_next(seeded, "w3") == jid
    seeded.refresh(job)
    assert job.attempts == 2 and job.worker_id == "w3"
    # dies again with no attempts left: failed, not retried forever
    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    seeded.commit()
    assert recover_stale_jobs(seeded) == {"retried": 0, "failed": 1}
    seeded.refresh(job)
    assert job.status == "failed" and job.finished_at is not None
    assert claim_job(seeded, jid, "w4") is False


def test_api_key_enforced_against_database_routes(seeded, monkeypatch):
    monkeypatch.setenv("CATRIX_DATABASE_URL", integration_db_url())
    monkeypatch.setenv("CATRIX_API_KEY", "integration-key")
    from catrix.api.main import create_app
    from catrix.config import get_settings

    get_settings.cache_clear()
    client = TestClient(create_app())
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/portfolio").status_code == 401
    ok = client.get("/api/v1/portfolio", headers={"X-API-Key": "integration-key"})
    assert ok.status_code == 200 and isinstance(ok.json(), list)
    runs = client.get("/api/v1/jobs/assessment-runs", headers={"X-API-Key": "integration-key"})
    assert runs.status_code == 200 and any(r["status"] == "completed" for r in runs.json())
    get_settings.cache_clear()


def test_demo_runs_without_network_and_reports_the_first_look(seeded):
    from catrix.pipeline.demo import format_report, run_demo

    report = run_demo(seeded, name="demo-test", allow_network=False)
    assert report["status"] == "completed", report["error"]
    assert report["rows"] == {
        "received": 25,
        "valid": 24,
        "invalid": 1,
        "exact_duplicates_excluded": 1,
        "shared_location": 1,
        "distinct_locations": 22,
    }
    assert report["financial"]["total_insured_value"] == 17_630_000
    assert report["summary"]["n_exposures"] == 23 and report["location_accumulation"]
    assert isinstance(report["stress_events"], list)
    assert '"portfolio_id"' in format_report(report)

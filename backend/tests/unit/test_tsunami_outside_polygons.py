"""The CGS tsunami layer publishes "No, Outside Hazard Area" polygons too: containment in
one of those must read as *not* in the hazard area (bug found on 2026-10-15: 14 of 23
example exposures, including Napa and San Jose, were reported inside the hazard area)."""

from __future__ import annotations

import pandas as pd

from catrix.hazards import secondary
from catrix.hazards.secondary import TsunamiModule, tsunami_polygon_is_hazard


def test_polygon_label_decides_hazard():
    assert tsunami_polygon_is_hazard({"attributes": {"Evacuate": "Yes, Tsunami Hazard Area"}})
    assert not tsunami_polygon_is_hazard({"attributes": {"Evacuate": "No, Outside Hazard Area"}})
    assert tsunami_polygon_is_hazard({"classification": "Yes, Tsunami Hazard Area"})
    assert not tsunami_polygon_is_hazard({})


def test_module_treats_outside_polygons_as_not_in_hazard_area(monkeypatch):
    monkeypatch.setattr(secondary, "source_state", lambda *_a: ("ingested", "v1"))
    monkeypatch.setattr(secondary, "polygon_count", lambda *_a: 2)
    monkeypatch.setattr(
        secondary,
        "polygon_lookup",
        lambda *_a, **_k: {
            0: {
                "classification": "No, Outside Hazard Area",
                "attributes": {"Evacuate": "No, Outside Hazard Area"},
            },
            1: {
                "classification": "Yes, Tsunami Hazard Area",
                "attributes": {"Evacuate": "Yes, Tsunami Hazard Area"},
            },
        },
    )
    pts = pd.DataFrame({"lon": [-122.29, -122.40], "lat": [38.30, 37.80]})
    out = TsunamiModule().assess(None, pts)
    assert [r.coverage_status for r in out] == ["not_in_hazard_area", "covered"]

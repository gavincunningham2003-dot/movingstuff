from catrix.hazards.earthquake import pga_class
from catrix.hazards.flood import classify_zone
from catrix.hazards.registry import build_modules, catalogue
from catrix.hazards.secondary import ExtremeRainfallModule


def test_flood_zone_classification():
    assert classify_zone("AE", None, "T")[1] == 3.0
    assert classify_zone("VE", None, None)[1] == 3.0
    assert classify_zone("X", "0.2 PCT ANNUAL CHANCE FLOOD HAZARD", "F")[1] == 2.0
    assert classify_zone("X", "AREA OF MINIMAL FLOOD HAZARD", "F")[1] == 1.0
    assert classify_zone("D", None, "F")[1] == 0.0


def test_pga_bins():
    assert pga_class(0.1) == "Low" and pga_class(0.45) == "High" and pga_class(1.2) == "Very High"


def test_atlas14_parser_handles_expected_layout():
    rows = ["['1','2','3','4','5','6','7','8','9','10']"] * 9 + [
        "['50','60','70','80','90','100','123.4','130','140','150']"
    ]
    text = "quantiles = [" + ",".join(rows) + "];"
    assert ExtremeRainfallModule.parse_atlas14(text) == 123.4
    assert ExtremeRainfallModule.parse_atlas14("garbage") is None


def test_catalogue_has_unique_ids_and_scale_notes():
    infos = catalogue()
    ids = [i.hazard_id for i in infos]
    assert len(ids) == len(set(ids)) and len(ids) >= 11
    assert all(i.scale_note for i in infos)
    assert any(i.probability_horizon == "2% in 50 years" for i in infos)
    assert len(build_modules(allow_network=False)) == len(infos)

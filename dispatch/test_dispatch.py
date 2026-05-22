"""Smoke tests for the Truer Foods dispatch tool."""
import csv
import json
from pathlib import Path

from openpyxl import load_workbook

import add
import dispatch
import extract
import zones

SAMPLE = Path(__file__).parent / "sample_orders.csv"


def test_extract_fsa():
    assert zones.extract_fsa("V6X 2C9") == "V6X"
    assert zones.extract_fsa("V6X2C9") == "V6X"
    assert zones.extract_fsa("1133 W Broadway Vancouver BC V6H 1G1") == "V6H"
    assert zones.extract_fsa("V6X") == "V6X"
    assert zones.extract_fsa("") is None
    assert zones.extract_fsa("no postal here") is None


def test_zone_for_known_areas():
    assert zones.zone_for("V6X 2C9")[0] == "Richmond"
    assert zones.zone_for("V5C 2H8")[0] == "Burnaby"
    assert zones.zone_for("V7P 2R5")[0] == "North/West Van"
    assert zones.zone_for("V4A 2H9")[0] == "White Rock"


def test_zone_for_unknown():
    assert zones.zone_for("Z9Z 9Z9") == ("Out of Area", 99)
    assert zones.zone_for("") == ("Out of Area", 99)


def test_load_sample():
    stops = dispatch.load_stops(SAMPLE)
    assert len(stops) == 14
    assert any(s.customer == "Tojo's Restaurant" for s in stops)


def test_sort_is_by_drive_order():
    stops = dispatch.sort_stops(dispatch.load_stops(SAMPLE))
    orders = [s.drive_order for s in stops]
    assert orders == sorted(orders)
    # First stops should be Richmond, last should be Surrey/WhiteRock area.
    assert stops[0].zone == "Richmond"


def test_split_drivers_balances_and_preserves_stops():
    stops = dispatch.sort_stops(dispatch.load_stops(SAMPLE))
    for n in (1, 2, 3, 4):
        routes = dispatch.split_drivers(stops, n)
        assert sum(len(r) for r in routes) == len(stops)
        assert len(routes) <= n
        for r in routes:
            assert r, "should not produce empty routes"


def test_split_keeps_zones_contiguous():
    stops = dispatch.sort_stops(dispatch.load_stops(SAMPLE))
    routes = dispatch.split_drivers(stops, 3)
    for route in routes:
        # Zones inside a route should appear in non-decreasing drive_order
        orders = [s.drive_order for s in route]
        assert orders == sorted(orders)


def test_prompt_stop_speed_entry(monkeypatch):
    inputs = iter([
        "Tojo's @ 1133 W Broadway Vancouver BC V6H 1G1",
        "3", "fresh", "AM only", "Back door",
    ])
    monkeypatch.setattr("builtins.input", lambda *a, **kw: next(inputs))
    stop = add.prompt_stop(1)
    assert stop["customer"] == "Tojo's"
    assert stop["address"] == "1133 W Broadway Vancouver BC V6H 1G1"
    assert stop["postal_code"] == "V6H"
    assert stop["boxes"] == "3"
    assert stop["product_type"] == "fresh"
    assert stop["window"] == "AM only"
    assert stop["notes"] == "Back door"


def test_prompt_stop_two_step_entry(monkeypatch):
    inputs = iter([
        "Sushi Mart",
        "5731 No 3 Rd Richmond BC V6X 2C9",
        "", "", "", "",
    ])
    monkeypatch.setattr("builtins.input", lambda *a, **kw: next(inputs))
    stop = add.prompt_stop(1)
    assert stop["customer"] == "Sushi Mart"
    assert stop["postal_code"] == "V6X"
    assert stop["boxes"] == "1"  # default


def test_prompt_stop_blank_returns_none(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "")
    assert add.prompt_stop(1) is None


def test_add_main_appends_and_dispatches(tmp_path, monkeypatch):
    csv_path = tmp_path / "today.csv"
    out_path = tmp_path / "today.xlsx"
    inputs = iter([
        "Tojo's @ 1133 W Broadway Vancouver BC V6H 1G1",
        "2", "fresh", "", "",
        "Sushi Mart @ 5731 No 3 Rd Richmond BC V6X 2C9",
        "3", "mixed", "AM only", "Dolly",
        "",  # end of entry
        "y",  # generate now
        "2",  # drivers
        str(out_path),  # output filename
    ])
    monkeypatch.setattr("builtins.input", lambda *a, **kw: next(inputs))
    rc = add.main(["--file", str(csv_path)])
    assert rc == 0
    rows = list(csv.DictReader(csv_path.open()))
    assert len(rows) == 2
    assert rows[0]["customer"] == "Tojo's"
    assert rows[1]["postal_code"] == "V6X"
    assert out_path.exists()
    wb = load_workbook(out_path)
    assert "Driver 1" in wb.sheetnames
    assert "Driver 2" in wb.sheetnames


def test_extract_parse_basic():
    raw = json.dumps({"stops": [
        {"customer": "Tojo's", "address": "1133 W Broadway Vancouver BC V6H 1G1",
         "boxes": 3, "product_type": "fresh", "window": "AM only",
         "notes": "Back door"}
    ]})
    stops = extract.parse_response(raw)
    assert len(stops) == 1
    s = stops[0]
    assert s["customer"] == "Tojo's"
    assert s["postal_code"] == "V6H"
    assert s["boxes"] == "3"
    assert s["product_type"] == "fresh"


def test_extract_parse_strips_markdown_fences():
    raw = '```json\n{"stops": [{"customer": "X", "address": "5731 No 3 Rd Richmond BC V6X 2C9"}]}\n```'
    stops = extract.parse_response(raw)
    assert stops[0]["postal_code"] == "V6X"


def test_extract_parse_skips_incomplete():
    raw = json.dumps({"stops": [
        {"customer": "Has no address"},
        {"address": "Has no customer"},
        {"customer": "Good", "address": "5731 No 3 Rd Richmond BC V6X 2C9"},
    ]})
    stops = extract.parse_response(raw)
    assert len(stops) == 1
    assert stops[0]["customer"] == "Good"


def test_extract_parse_defaults_boxes_to_1():
    raw = json.dumps({"stops": [
        {"customer": "X", "address": "5731 No 3 Rd Richmond BC V6X 2C9"}
    ]})
    stops = extract.parse_response(raw)
    assert stops[0]["boxes"] == "1"


def test_extract_main_with_image_appends(tmp_path, monkeypatch):
    img = tmp_path / "scr.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    csv_path = tmp_path / "today.csv"
    monkeypatch.setattr(extract, "call_claude", lambda *a, **kw: json.dumps({
        "stops": [
            {"customer": "Sushi Mart", "address": "5731 No 3 Rd Richmond BC V6X 2C9", "boxes": 6},
            {"customer": "Tojo's", "address": "1133 W Broadway Vancouver BC V6H 1G1", "boxes": 3, "product_type": "fresh"},
        ]
    }))
    rc = extract.main(["--image", str(img), "--file", str(csv_path),
                       "--yes", "--no-dispatch"])
    assert rc == 0
    rows = list(csv.DictReader(csv_path.open()))
    assert len(rows) == 2
    assert rows[0]["customer"] == "Sushi Mart"
    assert rows[0]["postal_code"] == "V6X"
    assert rows[1]["product_type"] == "fresh"


def test_extract_main_paste_mode(tmp_path, monkeypatch):
    csv_path = tmp_path / "today.csv"
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": staticmethod(lambda: "raw text")})())
    monkeypatch.setattr(extract, "call_claude", lambda *a, **kw: json.dumps({
        "stops": [{"customer": "Y", "address": "4022 Hastings St Burnaby BC V5C 2H8"}]
    }))
    rc = extract.main(["--paste", "--file", str(csv_path), "--yes", "--no-dispatch"])
    assert rc == 0
    rows = list(csv.DictReader(csv_path.open()))
    assert len(rows) == 1
    assert rows[0]["postal_code"] == "V5C"


def test_end_to_end(tmp_path):
    out = tmp_path / "routes.xlsx"
    rc = dispatch.main([str(SAMPLE), "--drivers", "2", "--out", str(out)])
    assert rc == 0
    assert out.exists()
    wb = load_workbook(out)
    assert "Summary" in wb.sheetnames
    assert "Driver 1" in wb.sheetnames
    assert "Driver 2" in wb.sheetnames
    # Driver 1 should have header row + data rows
    d1 = wb["Driver 1"]
    assert d1.max_row > 4

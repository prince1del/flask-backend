"""Article Master from any company sheet — a made-up "Sunrise Mills" booking sheet in the messy shape real
sheets have: header on row 4, a section row, blank merged brand cells, order-qty columns, % columns."""
from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from app import hop_article_sheet as S


def _sheet(extra_brand_row: bool = False) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Booking"
    ws.append(["Sunrise Mills - Winter booking sheet"])
    ws.append(["Distributor :-", "", "Date"])
    ws.append([])
    ws.append(["Sl No", "Product", "Brand", "TC", "Size", "Shade", "Bale Pack", "Retailer MD", "AWD MU",
               "MRP", "PTR", "Ex-Mill", "Booking Qty", "Delivery"])
    ws.append(["Cotton Collection"])
    ws.append([1, "Bedsheet", "Meadow", 144, "DB 224x254", "Blue", 18, 0.30, 0.15, 1000, 700, 608.70, None, "Oct"])
    ws.append([2, None, None, 144, "KS 274x274", "Blue", 12, 0.30, 0.15, 1400, 980, 852.17, None, "Oct"])
    ws.append([3, "Bedsheet", "Meadow", 144, "DB 224x254", "Pink", 18, 0.30, 0.15, 1000, 900, 608.70, None, "Oct"])
    ws.append(["Towel Collection"])
    ws.append([4, "Towel", "Breeze", None, "40x60", "White", 96, 0.33, 0.17, 150, 100.50, 85.90, 20, "Sep"])
    if extra_brand_row:
        ws.append([5, "Towel", "Breeze", None, "75x150", "White", 48, 0.33, 0.17, 600, 402, 343.59, None, "Sep"])
    ws.append(["Total", None, None, None, None, None, None, None, None, None, None, None, 20])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_analyze_finds_header_and_guesses_roles():
    a = S.analyze(_sheet())
    assert a["header_row"] == 4
    roles = {c["header"]: c["role"] for c in a["columns"]}
    assert roles["Ex-Mill"] == "purchase_price" and roles["PTR"] == "selling_price" and roles["MRP"] == "mrp"
    assert roles["Product"] == "category" and roles["Brand"] == "name_part" and roles["Size"] == "name_part"
    assert roles["Retailer MD"] == "detail" and roles["AWD MU"] == "detail" and roles["Bale Pack"] == "detail"
    assert roles["Booking Qty"] == "skip" and roles["Delivery"] == "skip" and roles["Sl No"] == "skip"


def test_build_names_sections_fill_down_and_checks():
    arts = S.build(S.analyze(_sheet()), season="W26")
    names = [x["name"] for x in arts]
    assert names[0] == "Meadow 144 TC DB 224x254 Blue"
    assert names[1] == "Meadow 144 TC KS 274x274 Blue"            # brand + product copied down into the blank cells
    assert arts[1]["category"] == "Bedsheet"
    assert "Cotton Collection" not in names and "Total" not in " ".join(names)
    assert arts[0]["details"]["Collection"] == "Cotton Collection"
    assert arts[3]["details"]["Collection"] == "Towel Collection"
    assert arts[0]["details"]["Retailer MD"] == "30%" and arts[0]["details"]["Bale Pack"] == "18"
    assert arts[0]["purchase_price"] == 608.7 and arts[0]["selling_price"] == 700 and arts[0]["season"] == "W26"
    assert arts[0]["checks"] == []                                   # 1000 − 30% = 700; 608.70 × 1.15 = 700
    assert any("does not match" in c for c in arts[2]["checks"])     # PTR 900 is not MRP − 30%
    assert "Booking Qty" not in arts[3]["details"]


def test_roles_can_be_changed():
    a = S.analyze(_sheet())
    shade = next(c["i"] for c in a["columns"] if c["header"] == "Shade")
    arts = S.build(a, {str(shade): "detail"})
    # Blue and Pink now share a name → the shade is added to tell them apart
    assert arts[0]["name"] == "Meadow 144 TC DB 224x254 · Blue" and arts[0]["details"]["Shade"] == "Blue"
    assert arts[1]["name"] == "Meadow 144 TC KS 274x274"


@pytest.fixture
def api(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, h, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    return client, {**h, "X-Nexora-UI": "distributor"}


def _read(client, h, data: bytes, **form):
    return client.post("/api/v1/hop/dist/price-list/read", headers=h, content_type="multipart/form-data",
                       data={"file": (io.BytesIO(data), "sunrise.xlsx"), **form}).get_json()


def test_read_apply_remember_and_search(api):
    import json

    client, h = api
    d = _read(client, h, _sheet(), season="W26")["data"]
    assert d["read_by"] == "excel" and d["new"] == 4 and d["sheet"]["remembered"] is False
    shade = next(c["i"] for c in d["sheet"]["columns"] if c["header"] == "Shade")
    # user keeps Shade as a detail (not in the name) → read again with that choice
    roles = {**d["sheet"]["roles"], str(shade): "detail"}
    d2 = _read(client, h, _sheet(), season="W26", roles=json.dumps(roles))["data"]
    names = [r["name"] for r in d2["rows"]]
    assert "Meadow 144 TC DB 224x254 · Blue" in names or "Meadow 144 TC DB 224x254" in " ".join(names)
    ok = client.post("/api/v1/hop/dist/price-list/apply", headers=h,
                     json={"rows": d2["rows"], "sig": d2["sheet"]["sig"], "roles": roles,
                           "headers": [c["header"] for c in d2["sheet"]["columns"]]}).get_json()
    assert ok["success"] and ok["data"]["created"] == 4
    arts = client.get("/api/v1/hop/dist/articles?q=towel collection", headers=h).get_json()["data"]["articles"]
    assert len(arts) == 1 and arts[0]["details"]["Bale Pack"] == "96" and arts[0]["purchase_price"] == 85.9
    # next season: same layout → roles remembered, prices compared, new article added
    d3 = _read(client, h, _sheet(extra_brand_row=True))["data"]
    assert d3["sheet"]["remembered"] is True and d3["sheet"]["roles"][str(shade)] == "detail"
    assert d3["new"] == 1 and d3["same"] >= 3


def test_ai_columns_route(api, monkeypatch):
    from app.services import hop_ai

    client, h = api
    monkeypatch.setattr(hop_ai, "ask_json", lambda prompt, **kw: {"roles": {"0": "name_part", "1": "purchase_price", "2": "bogus"}})
    r = client.post("/api/v1/hop/dist/price-list/ai-columns", headers=h,
                    json={"columns": [{"i": 0, "header": "Quality", "samples": ["Royal"]},
                                      {"i": 1, "header": "NLC Rate", "samples": ["450"]},
                                      {"i": 2, "header": "X", "samples": []}]}).get_json()
    assert r["data"]["roles"] == {"0": "name_part", "1": "purchase_price"}
    assert client.post("/api/v1/hop/dist/price-list/ai-columns", headers=h, json={}).status_code == 400

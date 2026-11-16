"""HoP Excel / CSV import of items and parties (made-up data)."""
from __future__ import annotations

import io
from pathlib import Path

import openpyxl

from app import hop_import as I
from app.hop_db import connect
from app.hop_ops import create_product, list_products
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema

ITEMS_CSV = """My item list,,,
Item Name,HSN,GST %,Sale Price,Opening Stock,Unknown col
Cushion,9404,12,450,10,x
Sheet,6302,5,abc,4,
Cushion,9404,12,460,1,
,,,,,
Lamp,9405,13,900,-2,
"""


def test_items_preview_commit_and_bulk_update(tmp_path: Path):
    p = str(tmp_path / "imp.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        create_product(conn, WS, {"name": "Lamp", "selling_price": 800})
        pv = I.preview(conn, WS, "items", I.read_table(ITEMS_CSV.encode(), "items.csv"))
        assert [c["field"] for c in pv["columns"]] == ["name", "hsn", "gst_pct", "selling_price", "stock_qty"]
        assert pv["ignored_columns"] == ["Unknown col"]
        st = [r["status"] for r in pv["rows"]]
        assert st == ["new", "new", "duplicate_in_file", "existing"]
        assert "sale price is not a number" in pv["rows"][1]["problems"][0].lower()
        lamp = pv["rows"][3]["problems"]
        assert any("negative" in x for x in lamp) and any("not a usual rate" in x for x in lamp)
        rows = [r["data"] for r in pv["rows"] if r["status"] in ("new", "existing")]
        out = I.commit(conn, WS, "items", rows)
        assert (out["added"], out["updated"], out["skipped"]) == (2, 0, 1)
        out = I.commit(conn, WS, "items", [{"name": "Lamp", "selling_price": 950}], update_existing=True)
        assert out["updated"] == 1
        assert {r["name"]: r["selling_price"] for r in list_products(conn, WS)}["Lamp"] == 950


def test_parties_from_excel(tmp_path: Path):
    p = str(tmp_path / "imp2.sqlite3")
    ensure_hop_schema(p)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Party Name", "Phone", "GSTIN", "Type", "Group"])
    ws.append(["Hotel One", "98765 43210", "27ABCDE1234F1Z5", "Customer", "Hotel"])
    ws.append(["Mill Two", "12345", "BADGST", "Supplier", ""])
    buf = io.BytesIO()
    wb.save(buf)
    with connect(p) as conn:
        pv = I.preview(conn, WS, "parties", I.read_table(buf.getvalue(), "parties.xlsx"))
        assert pv["rows"][0]["problems"] == [] and pv["rows"][1]["data"]["kind"] == "vendor"
        assert set(pv["rows"][1]["problems"]) == {"GSTIN looks wrong", "mobile number looks wrong"}
        out = I.commit(conn, WS, "parties", [r["data"] for r in pv["rows"]])
        assert out["added"] == 2
        assert conn.execute("SELECT customer_type FROM hop_customers WHERE company='Hotel One'").fetchone()[0] == "Hotel"
        assert conn.execute("SELECT COUNT(*) FROM hop_vendors WHERE company='Mill Two'").fetchone()[0] == 1
        assert I.commit(conn, WS, "parties", [{"company": "Hotel One"}])["skipped"] == 1


def test_import_api(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    r = client.post("/api/v1/hop/import/items/preview", headers=headers,
                    data={"file": (io.BytesIO(ITEMS_CSV.encode()), "i.csv")}, content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["data"]["counts"]["new"] >= 2
    r = client.post("/api/v1/hop/import/items/commit", headers=headers, json={"rows": [{"name": "Rug", "selling_price": 99}]})
    assert r.get_json()["data"]["added"] == 1
    bad = client.post("/api/v1/hop/import/items/preview", headers=headers,
                      data={"file": (io.BytesIO(b"a,b\n1,2\n"), "x.csv")}, content_type="multipart/form-data")
    assert bad.status_code == 400

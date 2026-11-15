"""HoP Phase 3 — item categories, units, party rates, due dates, reorder."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_settings
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_ops import create_product
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def db(tmp_path: Path):
    path = str(tmp_path / "hop_settings.sqlite3")
    ensure_hop_schema(path)
    return path


LINE = {"item_name": "Sofa", "qty": 2, "unit": "NOS", "rate": 5000, "tax_pct": 0}


def test_item_categories_and_units_seed_and_create(db):
    with connect(db) as conn:
        cats = hop_settings.list_item_categories(conn, WS)
        assert any(c["name"] == "Fabric" for c in cats)
        created = hop_settings.create_item_category(conn, WS, "Custom Soft")
        assert created["name"] == "Custom Soft"
        units = hop_settings.list_units(conn, WS)
        assert any(u["name"] == "MTR" for u in units)
        u = hop_settings.create_unit(conn, WS, "Bundle")
        assert u["name"] == "Bundle"


def test_product_stores_category_and_unit_masters(db):
    with connect(db) as conn:
        p = create_product(conn, WS, {"name": "Linen Sheet", "category": "Bed Linen", "unit": "Pcs",
                                      "selling_price": 900})
        assert p["category"] == "Bed Linen" and p["unit"] == "Pcs"
        assert p.get("category_id") and p.get("unit_id")
        cats = hop_settings.list_item_categories(conn, WS)
        assert any(c["name"] == "Bed Linen" and c["product_count"] >= 1 for c in cats)


def test_suggest_party_rate_from_last_invoice(db):
    with connect(db) as conn:
        cust = create_customer(conn, WS, {"company": "Hotel Rate"})
        prod = create_product(conn, WS, {"name": "Curtain", "selling_price": 100, "unit": "MTR"})
        create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "txn_date": "2026-10-01",
                          "lines": [{"item_name": "Curtain", "product_id": prod["id"], "qty": 10,
                                     "unit": "MTR", "rate": 250, "tax_pct": 5}]})
        sug = hop_settings.suggest_party_rate(
            conn, WS, party_type="customer", party_id=cust["id"], product_id=prod["id"]
        )
        assert sug["suggested_rate"] == 250 and sug["source"] == "party_last"


def test_entry_warnings_duplicate_and_unusual_price(db):
    with connect(db) as conn:
        cust = create_customer(conn, WS, {"company": "Hotel Warn"})
        prod = create_product(conn, WS, {"name": "Sofa Set", "selling_price": 10000})
        create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "txn_number": "INV-DUP-1",
                          "lines": [{"item_name": "Sofa Set", "product_id": prod["id"], "qty": 1,
                                     "unit": "Pcs", "rate": 10000, "tax_pct": 0}]})
        warns = hop_settings.invoice_entry_warnings(conn, WS, {
            "txn_type": 1, "party_type": "customer", "party_id": cust["id"],
            "txn_number": "INV-DUP-1",
            "lines": [{"item_name": "Sofa Set", "product_id": prod["id"], "rate": 20000, "tax_pct": 5, "hsn": "9401"}],
        })
        codes = {w["code"] for w in warns}
        assert "duplicate_bill_no" in codes
        assert "unusual_price" in codes


def test_entry_warnings_gst_guards(db):
    with connect(db) as conn:
        cust = create_customer(conn, WS, {"company": "Hotel GST"})
        conn.execute(
            "UPDATE hop_customers SET gst_type=?, gst_no=? WHERE id=?",
            ("Registered Business - Regular", "22AAAAA0000A1Z", cust["id"]),
        )
        conn.commit()
        warns = hop_settings.invoice_entry_warnings(conn, WS, {
            "txn_type": 1, "party_type": "customer", "party_id": cust["id"],
            "txn_number": "INV-GST-1",
            "lines": [{"item_name": "Curtain", "rate": 500, "tax_pct": 9}],
        })
        codes = {w["code"] for w in warns}
        assert "missing_gstin" in codes
        assert "missing_hsn" in codes
        assert "unusual_gst_rate" in codes


def test_due_date_from_credit_days(db):
    with connect(db) as conn:
        cust = create_customer(conn, WS, {"company": "Hotel Credit"})
        conn.execute("UPDATE hop_customers SET credit_days=15 WHERE id=?", (cust["id"],))
        conn.commit()
        inv = create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "txn_date": "2026-11-01",
                                "lines": [LINE]})
        assert inv["due_date"] == "2026-11-16"
        row = dict(conn.execute("SELECT due_date, balance_amount FROM hop_party_transactions WHERE id=?",
                                (inv["party_txn_id"],)).fetchone())
        assert row["due_date"] == "2026-11-16"
        overdue = hop_settings.overdue_bills(conn, WS, as_of="2026-12-01")
        assert overdue["count"] == 1 and overdue["bills"][0]["days_overdue"] >= 15


def test_low_stock_reorder_from_sales(db):
    with connect(db) as conn:
        cust = create_customer(conn, WS, {"company": "Hotel Stock"})
        prod = create_product(conn, WS, {"name": "Pillow", "stock_qty": 0, "unit": "Pcs",
                                         "selling_price": 200})
        create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "txn_date": "2026-11-01",
                          "lines": [{"item_name": "Pillow", "product_id": prod["id"], "qty": 30,
                                     "unit": "Pcs", "rate": 200, "tax_pct": 0}]})
        out = hop_settings.low_stock_reorder(conn, WS, days=30, low_at=0)
        item = next(i for i in out["items"] if i["id"] == prod["id"])
        assert item["sold_last_days"] == 30
        assert item["suggested_reorder_qty"] is not None


def test_api_routes(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    cats = client.get("/api/v1/hop/item-categories", headers=headers)
    assert cats.status_code == 200 and cats.get_json()["data"]["categories"]
    units = client.get("/api/v1/hop/units", headers=headers)
    assert units.status_code == 200 and units.get_json()["data"]["units"]
    assert client.get("/api/v1/hop/overdue-bills", headers=headers).status_code == 200
    assert client.get("/api/v1/hop/stock/reorder", headers=headers).status_code == 200
    # suggest-rate needs party_id
    bad = client.get("/api/v1/hop/suggest-rate?party_type=customer&party_id=0&item_name=x", headers=headers)
    assert bad.status_code == 400

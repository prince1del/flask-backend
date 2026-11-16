"""HoP item extras: service items, wholesale, 2nd unit, barcode, custom fields (made-up company)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_settings
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_ops import create_product, list_products, update_product
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def db(tmp_path: Path):
    p = str(tmp_path / "items.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        cust = create_customer(conn, WS, {"company": "Shop I"})
        conn.commit()
    return p, cust["id"]


def _stock(conn, name):
    return conn.execute("SELECT stock_qty FROM hop_products WHERE name=?", (name,)).fetchone()[0]


def test_service_item_never_moves_stock(db):
    p, cust = db
    with connect(p) as conn:
        create_product(conn, WS, {"name": "Stitching", "item_type": "service", "selling_price": 300})
        create(conn, WS, {"txn_type": 1, "customer_id": cust, "lines": [{"item_name": "Stitching", "qty": 4, "rate": 300}]})
        assert _stock(conn, "Stitching") == 0
        w = hop_settings.invoice_entry_warnings(conn, WS, {"txn_type": 1, "party_id": cust,
                                                           "lines": [{"item_name": "Stitching", "qty": 4, "rate": 300}]})
        assert not any(x["code"] == "short_stock" for x in w)


def test_second_unit_converts_stock(db):
    p, cust = db
    with connect(p) as conn:
        create_product(conn, WS, {"name": "Hook", "unit": "PCS", "stock_qty": 100, "alt_unit": "box", "alt_unit_factor": 10})
        create(conn, WS, {"txn_type": 1, "customer_id": cust, "lines": [{"item_name": "Hook", "qty": 3, "unit": "BOX", "rate": 50}]})
        assert _stock(conn, "Hook") == 70
        create(conn, WS, {"txn_type": 1, "customer_id": cust, "lines": [{"item_name": "Hook", "qty": 5, "unit": "PCS", "rate": 5}]})
        assert _stock(conn, "Hook") == 65
        with pytest.raises(ValueError):
            create_product(conn, WS, {"name": "Bad", "alt_unit": "BOX"})


def test_wholesale_barcode_custom_fields_and_checks(db):
    p, cust = db
    with connect(p) as conn:
        prod = create_product(conn, WS, {"name": "Curtain", "selling_price": 1200, "purchase_price": 700, "mrp": 1100,
                                         "wholesale_price": 950, "wholesale_min_qty": 10, "barcode": "8901234",
                                         "custom_fields": {"Colour": "Blue"}})
        assert prod["custom_fields"] == {"Colour": "Blue"} and prod["item_type"] == "product"
        assert any("above MRP" in c for c in prod["checks"])
        small = hop_settings.suggest_party_rate(conn, WS, party_type="customer", party_id=cust, product_id=prod["id"], qty=2)
        big = hop_settings.suggest_party_rate(conn, WS, party_type="customer", party_id=cust, product_id=prod["id"], qty=12)
        assert (small["suggested_rate"], big["suggested_rate"], big["source"]) == (1200, 950, "wholesale")
        assert [r["name"] for r in list_products(conn, WS, "8901234")] == ["Curtain"]
        other = create_product(conn, WS, {"name": "Rod"})
        with pytest.raises(ValueError):
            update_product(conn, WS, other["id"], {"barcode": "8901234"})


def test_barcode_api(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    r = client.post("/api/v1/hop/products", headers=headers, json={"name": "Lamp B", "barcode": "555"})
    assert r.status_code in (200, 201), r.get_json()
    r = client.get("/api/v1/hop/products/by-barcode?code=555", headers=headers)
    assert r.status_code == 200 and r.get_json()["data"]["name"] == "Lamp B"
    assert client.get("/api/v1/hop/products/by-barcode?code=000", headers=headers).status_code == 404


def test_ask_nexora_items(db):
    from app.services import hop_ai

    p, _ = db
    with connect(p) as conn:
        create_product(conn, WS, {"name": "Sofa", "selling_price": 5000, "mrp": 4000, "barcode": "777"})
        out = hop_ai.books_answer(conn, WS, "items", "777")
        assert out["items"][0]["name"] == "Sofa" and out["low_or_negative_stock"][0]["name"] == "Sofa"
        assert "above MRP" in out["item_problems"][0]["problems"][0]


def test_wholesale_by_item_name(db):
    p, cust = db
    with connect(p) as conn:
        create_product(conn, WS, {"name": "Blind", "selling_price": 800, "wholesale_price": 650, "wholesale_min_qty": 5})
        r = hop_settings.suggest_party_rate(conn, WS, party_type="customer", party_id=cust, item_name="blind", qty=6)
        assert (r["suggested_rate"], r["source"]) == (650, "wholesale")

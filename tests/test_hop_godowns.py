"""HoP godowns: stock per godown, transfers, bills from a godown (made-up data)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_godowns as G
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_ops import create_product
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


def test_godown_flow(tmp_path: Path):
    p = str(tmp_path / "g.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        cust = create_customer(conn, WS, {"company": "Shop G"})
        prod = create_product(conn, WS, {"name": "Rug", "stock_qty": 50, "purchase_price": 100})
        main = G.default_id(conn, WS)
        shop = G.create(conn, WS, "Showroom")["id"]
        with pytest.raises(ValueError):
            G.create(conn, WS, "showroom")
        G.transfer(conn, WS, prod["id"], main, shop, 20)
        row = G.stock_by_godown(conn, WS, prod["id"])[0]
        assert row["by_godown"] == {shop: 20, main: 30} and row["total"] == 50
        with pytest.raises(ValueError):
            G.transfer(conn, WS, prod["id"], shop, main, 25)
        # A sale from the showroom takes stock from there only.
        create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "godown_id": shop,
                          "lines": [{"item_name": "Rug", "qty": 5, "rate": 300}]})
        row = G.stock_by_godown(conn, WS, prod["id"])[0]
        assert row["by_godown"] == {shop: 15, main: 30} and row["total"] == 45
        gl = {g["name"]: g for g in G.list_godowns(conn, WS)}
        assert gl["Showroom"]["stock_value"] == 1500 and gl["Main godown"]["is_default"] == 1
        with pytest.raises(ValueError):
            create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "godown_id": 999,
                              "lines": [{"item_name": "Rug", "qty": 1, "rate": 300}]})


def test_godown_api(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    r = client.post("/api/v1/hop/godowns", headers=headers, json={"name": "Warehouse"})
    assert r.status_code == 201
    d = client.get("/api/v1/hop/godowns", headers=headers).get_json()["data"]
    assert [g["name"] for g in d["godowns"]] == ["Main godown", "Warehouse"]


def test_godown_warning_and_ask(tmp_path: Path):
    from app import hop_settings
    from app.services import hop_ai

    p = str(tmp_path / "g2.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        cust = create_customer(conn, WS, {"company": "Shop H"})
        create_product(conn, WS, {"name": "Lamp", "stock_qty": 10})
        shop = G.create(conn, WS, "Showroom")["id"]
        w = hop_settings.invoice_entry_warnings(conn, WS, {"txn_type": 1, "party_id": cust["id"], "godown_id": shop,
                                                           "lines": [{"item_name": "Lamp", "qty": 3, "rate": 10}]})
        assert any(x["code"] == "short_stock_godown" for x in w)
        out = hop_ai.books_answer(conn, WS, "items", "Lamp")
        assert out["godowns"][0]["godown"] == "Main godown"

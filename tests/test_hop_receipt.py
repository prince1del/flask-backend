"""Thermal receipt for the Bluetooth printer: fits 32 / 48 columns, ASCII only, totals and balance right."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_receipt
from app.hop_db import connect, create_customer
from app.hop_doc_preview import build_txn_preview, create_manual_party_document as create
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def bill(tmp_path: Path):
    p = str(tmp_path / "rc.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        c = create_customer(conn, WS, {"company": "Sharma Electronics Pvt Ltd", "mobile": "9876543210"})
        t = create(conn, WS, {"txn_type": 1, "customer_id": c["id"], "txn_date": "2026-11-17", "lines": [
            {"item_name": "Daikin 1.5 Ton 5 Star Inverter Split Air Conditioner", "qty": 1, "rate": 41000, "tax_pct": 18},
            {"item_name": "Copper pipe", "qty": 3.5, "unit": "MTR", "rate": 450}]})
        conn.commit()
        data = build_txn_preview(conn, WS, party_txn_id=int(t["party_txn_id"]))
    return p, int(t["party_txn_id"]), data


@pytest.mark.parametrize("width", [32, 48])
def test_fits_paper_and_ascii(bill, width):
    _, _, data = bill
    r = hop_receipt.build(data, width)
    assert r["width"] == width
    texts = [x["text"] for x in r["lines"]]
    assert all(len(t) <= width for t in texts)
    assert all(t.isascii() for t in texts)
    joined = "\n".join(texts)
    assert "Daikin" in joined and "Copper pipe" in joined and "Sharma Electronics" in joined
    total = next(t for t in texts if t.startswith("TOTAL"))
    assert total.endswith(f"{data['totals']['grand_total']:,.2f}")
    assert any(t.startswith("Balance due") for t in texts)   # nothing paid yet


def test_paper_from_settings(bill):
    _, _, data = bill
    data["print"]["paper"] = "thermal80"
    assert hop_receipt.build(data)["width"] == 48
    data["print"]["paper"] = "a4"
    assert hop_receipt.build(data)["width"] == 32


def test_route(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    assert client.get("/api/v1/hop/party-transactions/999999/receipt", headers=headers).status_code == 404

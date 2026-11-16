"""HoP batch / expiry / serial on bill lines + expiry report (made-up data)."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from app import hop_reports
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create, get_party_transaction_edit_data
from app.hop_ops import create_vendor
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


def test_batches_and_expiry(tmp_path: Path):
    p = str(tmp_path / "b.sqlite3")
    ensure_hop_schema(p)
    soon = (date.today() + timedelta(days=20)).isoformat()
    late = (date.today() + timedelta(days=400)).isoformat()
    with connect(p) as conn:
        v = create_vendor(conn, WS, {"company": "Supplier B"})
        c = create_customer(conn, WS, {"company": "Buyer B"})
        create(conn, WS, {"txn_type": 2, "vendor_id": v["id"], "lines": [
            {"item_name": "Polish", "qty": 10, "rate": 50, "batch_no": "B1", "expiry_date": soon},
            {"item_name": "Polish", "qty": 5, "rate": 50, "batch_no": "B2", "expiry_date": late}]})
        inv = create(conn, WS, {"txn_type": 1, "customer_id": c["id"], "lines": [
            {"item_name": "Polish", "qty": 4, "rate": 90, "batch_no": "B1", "serial_no": "S-1, S-2"}]})
        r = hop_reports.run(conn, WS, "expiry", {"days": 60})
        assert [(x["batch"], x["qty_left"]) for x in r["rows"]] == [("B1", 6)] and r["expiring"] == 1
        ed = get_party_transaction_edit_data(conn, WS, inv["party_txn_id"])
        assert ed["lines"][0]["serial_no"] == "S-1, S-2" and ed["lines"][0]["batch_no"] == "B1"
        with pytest.raises(ValueError):
            create(conn, WS, {"txn_type": 2, "vendor_id": v["id"], "lines": [
                {"item_name": "Polish", "qty": 1, "rate": 50, "expiry_date": "31/12/2026"}]})


def test_batch_prints_on_bill(tmp_path: Path):
    from app.hop_doc_preview import build_txn_preview

    p = str(tmp_path / "b2.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        c = create_customer(conn, WS, {"company": "Buyer P"})
        inv = create(conn, WS, {"txn_type": 1, "customer_id": c["id"], "lines": [
            {"item_name": "Polish", "qty": 1, "rate": 90, "batch_no": "B9", "expiry_date": "2027-01-31", "free_qty": 1}]})
        d = build_txn_preview(conn, WS, party_txn_id=inv["party_txn_id"])["lines"][0]["description"]
        assert d == "Batch B9 · Exp 2027-01-31 · + 1 free"

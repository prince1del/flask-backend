"""HoP bill extras: additional charges, free qty, PO no/date, time, TCS (made-up company)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_reports
from app.hop_db import connect, create_customer
from app.hop_doc_preview import (
    build_txn_preview,
    create_manual_party_document as create,
    get_party_transaction_edit_data,
    update_manual_party_document as update,
)
from app.hop_ops import create_product
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def db(tmp_path: Path):
    p = str(tmp_path / "extras.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        cust = create_customer(conn, WS, {"company": "Hotel E"})
        create_product(conn, WS, {"name": "Lamp", "stock_qty": 20, "purchase_price": 100})
        conn.commit()
    return p, cust["id"]


def _bill(cust, **extra):
    d = {"txn_type": 1, "customer_id": cust, "txn_date": "2026-11-16",
         "lines": [{"item_name": "Lamp", "qty": 2, "free_qty": 1, "rate": 1000, "tax_pct": 18}],
         "charges": [{"name": "Freight", "amount": 200, "tax_pct": 18}, {"name": "Packing", "amount": 50}]}
    d.update(extra)
    return d


def test_charges_free_qty_po_time_tcs(db):
    p, cust = db
    with connect(p) as conn:
        inv = create(conn, WS, _bill(cust, po_number="PO-77", po_date="2026-11-10", txn_time="14:30", tcs_pct=1))
        t = dict(conn.execute("SELECT * FROM hop_party_transactions WHERE id=?", (inv["party_txn_id"],)).fetchone())
        # 2000 + 360 GST + 200 + 36 GST + 50 = 2646; TCS 1% = 26.46
        assert t["tcs_amount"] == 26.46 and round(t["total_amount"], 2) == 2672.46
        assert (t["po_number"], t["po_date"], t["txn_time"]) == ("PO-77", "2026-11-10", "14:30")
        # Stock: 2 sold + 1 free leaves 17; charges don't touch stock.
        assert conn.execute("SELECT stock_qty FROM hop_products WHERE name='Lamp'").fetchone()[0] == 17
        pv = build_txn_preview(conn, WS, party_txn_id=inv["party_txn_id"])
        assert pv["header"]["po_number"] == "PO-77" and pv["totals"]["tcs_amount"] == 26.46
        ed = get_party_transaction_edit_data(conn, WS, inv["party_txn_id"])
        assert [c["name"] for c in ed["charges"]] == ["Freight", "Packing"]
        assert all(ln["item_name"] == "Lamp" for ln in ed["lines"]) and ed["lines"][0]["free_qty"] == 1
        assert ed["tcs_pct"] == 1 and ed["txn_time"] == "14:30"


def test_tcs_in_books_and_edit(db):
    p, cust = db
    with connect(p) as conn:
        inv = create(conn, WS, _bill(cust, tcs_pct=1))
        tb = hop_reports.trial_balance(conn, WS)
        assert round(tb["total_debit"], 2) == round(tb["total_credit"], 2)
        bs = hop_reports.balance_sheet(conn, WS)
        names = {r["name"]: r["amount"] for sec in bs.get("liabilities", []) for r in (sec.get("rows") or [sec])
                 if isinstance(r, dict) and "name" in r}
        assert names.get("TCS collected (to deposit)") == 26.46
        # Edit: drop the charges and TCS — stock and total follow.
        update(conn, WS, inv["party_txn_id"], {"txn_type": 1, "customer_id": cust, "txn_date": "2026-11-16",
                                               "lines": [{"item_name": "Lamp", "qty": 1, "rate": 1000}]})
        t = dict(conn.execute("SELECT * FROM hop_party_transactions WHERE id=?", (inv["party_txn_id"],)).fetchone())
        assert round(t["total_amount"], 2) == 1000 and not t["tcs_amount"]
        assert conn.execute("SELECT stock_qty FROM hop_products WHERE name='Lamp'").fetchone()[0] == 19


def test_tcs_only_on_sale_and_capped(db):
    p, cust = db
    with connect(p) as conn:
        with pytest.raises(ValueError):
            create(conn, WS, _bill(cust, tcs_pct=9))
        conn.rollback()
        est = create(conn, WS, _bill(cust, txn_type=27, tcs_pct=1))
        t = conn.execute("SELECT tcs_amount FROM hop_party_transactions WHERE id=?", (est["party_txn_id"],)).fetchone()
        assert not t[0]


def test_tcs_on_purchase_bill_is_an_asset(db):
    from app.hop_ops import create_vendor
    p, _ = db
    with connect(p) as conn:
        v = create_vendor(conn, WS, {"company": "Mill V"})
        create(conn, WS, {"txn_type": 2, "vendor_id": v["id"], "txn_date": "2026-11-16", "tcs_pct": 0.1,
                          "lines": [{"item_name": "Lamp", "qty": 10, "rate": 100}]})
        bs = hop_reports.balance_sheet(conn, WS)
        assert {a["name"]: a["amount"] for a in bs["assets"]}.get("TCS paid (to claim)") == 1.0
        assert bs["balanced"]

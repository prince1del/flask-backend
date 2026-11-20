"""Goods received: a company's CI is in transit; stock comes in when the goods arrive (made-up companies)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_dist_extra as X
from app import hop_dist_receive as R
from app import hop_distributor as D
from app.hop_db import connect
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_ops import create_vendor
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def firm(tmp_path: Path):
    path = str(tmp_path / "gr.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        R.ensure_schema(conn)
        a = create_vendor(conn, WS, {"company": "Alpha Mills"})
        b = create_vendor(conn, WS, {"company": "Beta Linen"})
        loose = create_vendor(conn, WS, {"company": "Local Supplier"})      # not a company I distribute for
        for v in (a, b):
            D.set_principal(conn, WS, v["id"], True)
        t = X.save_article(conn, WS, {"code": "T-1", "name": "Alpha Towel", "mrp": 300, "purchase_price": 150, "vendor_id": a["id"]})
        s = X.save_article(conn, WS, {"code": "S-1", "name": "Beta Sheet", "mrp": 900, "purchase_price": 500, "vendor_id": b["id"]})
    return path, {"a": a["id"], "b": b["id"], "loose": loose["id"], "towel": t["id"], "sheet": s["id"]}


def _stock(conn, pid):
    return conn.execute("SELECT stock_qty FROM hop_products WHERE id=?", (pid,)).fetchone()[0] or 0


def test_ci_in_transit_then_received_in_parts(firm):
    path, ids = firm
    with connect(path) as conn:
        so = D.create_so(conn, WS, {"so_number": "SO-7", "so_date": "2026-11-01", "vendor_id": ids["a"],
                                    "lines": [{"item_name": "Alpha Towel", "qty": 100, "rate": 150}]})
        ci = create(conn, WS, {"txn_type": 2, "party_type": "vendor", "vendor_id": ids["a"], "txn_date": "2026-11-05",
                               "party_bill_no": "CI-1",
                               "lines": [{"item_name": "Alpha Towel", "product_id": ids["towel"], "qty": 60, "rate": 150},
                                         {"item_name": "Unknown thing", "qty": 5, "rate": 10}]})
        bid = ci["party_txn_id"]
        D.link_bill(conn, WS, so["id"], bid)
        assert _stock(conn, ids["towel"]) == 0                        # saved CI = in transit, no stock yet
        st = R.bill_status(conn, WS, bid)
        assert st["status"] == "in transit" and st["lines"][0]["pending"] == 60
        assert st["not_in_article_master"] == ["Unknown thing"] and st["company"] == "Alpha Mills"
        assert [b["ci_no"] for b in R.to_receive(conn, WS)["bills"]] == ["CI-1"]
        with pytest.raises(ValueError):
            R.receive(conn, WS, bid, {"lines": [{"product_id": ids["towel"], "qty": 61}]})   # more than billed
        st = R.receive(conn, WS, bid, {"lines": [{"product_id": ids["towel"], "qty": 40}], "received_on": "2026-11-08"})
        assert st["status"] == "part received" and _stock(conn, ids["towel"]) == 40
        j = conn.execute("SELECT vendor_id, doc_txn_id, so_id, reason FROM hop_stock_journals WHERE reference LIKE 'grn:%'").fetchone()
        assert (j[0], j[1], j[2]) == (ids["a"], bid, so["id"]) and "Alpha Mills" in j[3] and "CI-1" in j[3]
        st = R.receive(conn, WS, bid, {"lines": [{"product_id": ids["towel"], "qty": 20}], "received_on": "2026-11-09"})
        assert st["status"] == "received" and _stock(conn, ids["towel"]) == 60
        assert st["shipment"]["received_on"] == "2026-11-09"
        assert R.to_receive(conn, WS)["bills"] == []                 # nothing left to receive
        # editing the CI later keeps what was received (matched per article)
        st = R.undo_receipt(conn, WS, st["receipts"][-1]["id"])
        assert st["status"] == "part received" and _stock(conn, ids["towel"]) == 40
        by = {s["company"]: s for s in R.stock_by_company(conn, WS)}
        assert by["Alpha Mills"]["qty"] == 40 and by["Beta Linen"]["qty"] == 0
        assert "CI-1" in R.summary_text(conn, WS)


def test_other_suppliers_and_old_bills_keep_stock_on_save(firm):
    path, ids = firm
    with connect(path) as conn:
        # a supplier that is not a company I distribute for: stock on save, as before
        create(conn, WS, {"txn_type": 2, "party_type": "vendor", "vendor_id": ids["loose"], "txn_date": "2026-11-05",
                          "lines": [{"item_name": "Beta Sheet", "product_id": ids["sheet"], "qty": 3, "rate": 500}]})
        assert _stock(conn, ids["sheet"]) == 3
        # a company bill that already moved stock cannot be received again
        bill = create(conn, WS, {"txn_type": 2, "party_type": "vendor", "vendor_id": ids["b"], "txn_date": "2026-11-06",
                                 "lines": [{"item_name": "Beta Sheet", "product_id": ids["sheet"], "qty": 2, "rate": 500}]})
        conn.execute("UPDATE hop_party_transactions SET stock_mode=NULL WHERE id=?", (bill["party_txn_id"],))
        with pytest.raises(ValueError):
            R.receive(conn, WS, bill["party_txn_id"], {"lines": [{"product_id": ids["sheet"], "qty": 1}]})
        with pytest.raises(LookupError):
            R.bill_status(conn, WS, 99999)

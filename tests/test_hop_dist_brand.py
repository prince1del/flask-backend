"""Distributor brand desk on a made-up firm: setup steps, SO vs received (pending goods), shipments, money."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_dist_brand as BR
from app import hop_dist_extra as X
from app import hop_dist_terms as T
from app import hop_distributor as D
from app.hop_db import connect
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_ops import create_vendor
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def firm(tmp_path: Path):
    path = str(tmp_path / "br.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        BR.ensure_schema(conn)
        a = create_vendor(conn, WS, {"company": "Alpha Mills"})
        b = create_vendor(conn, WS, {"company": "Beta Motors"})
        for v in (a, b):
            D.set_principal(conn, WS, v["id"], True)
        t = X.save_article(conn, WS, {"code": "T-1", "name": "Alpha Towel", "mrp": 300, "purchase_price": 150, "vendor_id": a["id"]})
    return path, a["id"], b["id"], t["id"]


def test_desk_pending_goods_shipment_and_steps(firm):
    path, aid, bid, tid = firm
    with connect(path) as conn:
        d0 = BR.brand_desk(conn, WS, aid)
        # setup = registering the brand: details, terms, price rule (orders / SO / CI are not setup)
        assert d0["setup_done"] == 0 and d0["setup_total"] == 3
        assert [s["key"] for s in d0["steps"]] == ["details", "terms", "rule"]
        T.save_terms(conn, WS, aid, {"payment_terms": "30 days", "price_rule": "markup", "rule_pct": 20})
        so = D.create_so(conn, WS, {"so_number": "SO-1", "so_date": "2026-11-01", "vendor_id": aid,
                                    "lines": [{"item_name": "Alpha Towel", "qty": 100, "rate": 150}]})
        ci = create(conn, WS, {"txn_type": 2, "party_type": "vendor", "vendor_id": aid, "txn_date": "2026-11-05",
                               "party_bill_no": "INV-1",
                               "lines": [{"item_name": "Alpha Towel", "product_id": tid, "qty": 60, "rate": 150, "tax_pct": 5}]})
        D.link_bill(conn, WS, so["id"], ci["party_txn_id"])
        d = BR.brand_desk(conn, WS, aid)
        p = d["pending_items"][0]
        assert p["on_so"] == 100 and p["received"] == 60 and p["pending"] == 40 and p["pending_value"] == 6000
        assert d["money"]["billed"] > 0 and d["setup_done"] == 2          # details not filled yet
        # shipment: in transit, late, then received
        sh = BR.save_shipment(conn, WS, ci["party_txn_id"], {"lr_no": "LR-9", "transporter": "VRL", "dispatched_on": "2026-11-05",
                                                            "eta": "2026-11-06"})
        assert sh["in_transit"][0]["lr_no"] == "LR-9" and sh["late"] == 1
        with pytest.raises(ValueError):
            BR.save_shipment(conn, WS, ci["party_txn_id"], {"eta": "06-11-2026"})
        sh = BR.save_shipment(conn, WS, ci["party_txn_id"], {"received_on": "2026-11-08"})
        assert not sh["in_transit"] and sh["received"][0]["lr_no"] == "LR-9"   # earlier fields kept
        # the other company's desk shows nothing of Alpha
        other = BR.brand_desk(conn, WS, bid)
        assert other["pending_items"] == [] and other["money"]["billed"] == 0
        all_ = {c["company"]: c for c in BR.desks(conn, WS)["companies"]}
        assert all_["Alpha Mills"]["pending_value"] == 6000 and all_["Beta Motors"]["pending_value"] == 0
        with pytest.raises(LookupError):
            BR.brand_desk(conn, WS, 99999)

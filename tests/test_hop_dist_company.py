"""Item card, hand stock with company, company edit, CD + incentives, per-company report (made-up companies)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_dist_company as C
from app import hop_dist_extra as X
from app import hop_dist_margins as M
from app import hop_dist_receive as R
from app import hop_dist_terms as T
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def firm(tmp_path: Path):
    path = str(tmp_path / "co.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        C.ensure_schema(conn)
        a = M.add_company(conn, WS, {"company": "Alpha Mills", "gst_no": "27ABCDE1234F1Z5"})
        b = M.add_company(conn, WS, {"company": "Beta Linen"})
        t = X.save_article(conn, WS, {"code": "T-1", "name": "Alpha Towel", "category": "Towels", "mrp": 300,
                                      "purchase_price": 150, "selling_price": 200, "vendor_id": a["id"]})
        cust = create_customer(conn, WS, {"company": "Shop One"})
    return path, {"a": a["id"], "b": b["id"], "towel": t["id"], "cust": cust["id"]}


def test_item_card_and_hand_stock_needs_company(firm):
    path, ids = firm
    with connect(path) as conn:
        with pytest.raises(ValueError):
            C.hand_stock(conn, WS, ids["towel"], {"qty": 10})                    # which company?
        card = C.hand_stock(conn, WS, ids["towel"], {"qty": 10, "vendor_id": ids["a"]})
        assert card["stock"] == 10 and card["company"] == "Alpha Mills"
        assert card["stock_lines"][0]["kind"] == "By hand" and card["stock_lines"][0]["company"] == "Alpha Mills"
        card = C.hand_stock(conn, WS, ids["towel"], {"qty": 4, "opening": True})
        assert card["stock"] == 14
        with pytest.raises(ValueError):
            C.hand_stock(conn, WS, ids["towel"], {"qty": -2})                    # why is it going out?
        card = C.hand_stock(conn, WS, ids["towel"], {"qty": -2, "reason": "damaged"})
        assert card["stock"] == 12
        # in transit on a company invoice shows on the card
        create(conn, WS, {"txn_type": 2, "party_type": "vendor", "vendor_id": ids["a"], "txn_date": "2026-11-05",
                          "party_bill_no": "CI-5", "lines": [{"item_name": "Alpha Towel", "product_id": ids["towel"], "qty": 30, "rate": 150}]})
        card = C.item_card(conn, WS, ids["towel"])
        assert card["to_come_qty"] == 30 and card["to_come"][0]["ci_no"] == "CI-5"
        with pytest.raises(LookupError):
            C.item_card(conn, WS, 99999)


def test_edit_company(firm):
    path, ids = firm
    with connect(path) as conn:
        c = C.edit_company(conn, WS, ids["a"], {"address": "2 Mill Road", "state": "Gujarat", "mobile": "9876543210",
                                                 "contact_person": "R. Shah", "contact_designation": "Area Sales Manager"})
        assert c["address"] == "2 Mill Road" and c["state"] == "Gujarat" and c["contact_designation"] == "Area Sales Manager"
        with pytest.raises(ValueError):
            C.edit_company(conn, WS, ids["a"], {"gst_no": "WRONG"})
        with pytest.raises(ValueError):
            C.edit_company(conn, WS, ids["a"], {"company": "Beta Linen"})       # name of another company


def test_cd_incentive_and_company_report(firm):
    path, ids = firm
    with connect(path) as conn:
        T.save_terms(conn, WS, ids["a"], {"cash_discount_pct": 2, "credit_days": 30})
        ci = create(conn, WS, {"txn_type": 2, "party_type": "vendor", "vendor_id": ids["a"], "txn_date": "2026-11-01",
                               "party_bill_no": "CI-9", "lines": [{"item_name": "Alpha Towel", "product_id": ids["towel"], "qty": 100, "rate": 150}]})
        R.receive(conn, WS, ci["party_txn_id"], {"lines": [{"product_id": ids["towel"], "qty": 100}], "received_on": "2026-11-03"})
        cd = C.cash_discount(conn, WS, ids["a"])
        assert cd["rows"][0]["cd"] == round(cd["rows"][0]["amount"] * 0.02, 2) and cd["rows"][0]["pay_by"] == "2026-12-01"
        # a company credit note pays the incentive
        cn = create(conn, WS, {"txn_type": 16, "party_type": "vendor", "vendor_id": ids["a"], "txn_date": "2026-11-20",
                               "party_bill_no": "CN-1", "lines": [{"item_name": "Incentive", "qty": 1, "rate": 300}]})
        inc = C.save_incentive(conn, WS, ids["a"], {"title": "AW26 target", "basis": "target", "amount": 300})
        assert inc["still_due"] == 300 and inc["credit_notes_not_linked"][0]["no"] == "CN-1"
        iid = inc["rows"][0]["id"]
        inc = C.save_incentive(conn, WS, ids["a"], {"id": iid, "title": "AW26 target", "amount": 300, "cn_txn_id": cn["party_txn_id"]})
        assert inc["rows"][0]["status"] == "received" and inc["still_due"] == 0
        with pytest.raises(ValueError):
            C.save_incentive(conn, WS, ids["a"], {"title": "x"})                 # no % or amount
        # sell 40 towels → margin per category
        create(conn, WS, {"txn_type": 1, "party_type": "customer", "customer_id": ids["cust"], "txn_date": "2026-11-10",
                          "lines": [{"item_name": "Alpha Towel", "product_id": ids["towel"], "qty": 40, "rate": 200}]})
        rep = C.company_report(conn, WS, ids["a"], "2026-04-01", "2027-03-31")
        assert rep["flow"]["invoiced_qty"] == 100 and rep["flow"]["received_qty"] == 100 and rep["flow"]["to_come_qty"] == 0
        towels = rep["by_category"][0]
        assert towels["category"] == "Towels" and towels["qty"] == 40 and towels["cost"] == 6000
        assert rep["margin"]["incentive_received"] == 300
        assert rep["margin"]["net"] == round(rep["margin"]["gross"] + rep["margin"]["cd_earned"] + 300, 2)
        assert "Alpha Mills" in C.summary_text(conn, WS)
        other = C.company_report(conn, WS, ids["b"], "2026-04-01", "2027-03-31")
        assert other["margin"]["sales"] == 0 and other["flow"]["invoiced_qty"] == 0

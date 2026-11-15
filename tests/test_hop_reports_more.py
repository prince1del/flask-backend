"""Phase 4 reports on the same small made-up company as tests/test_hop_reports.py."""
from __future__ import annotations

from app import hop_reports as R
from app.hop_db import connect
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_schema import HOP_WORKSPACE_ID as WS
from tests.test_hop_reports import co  # noqa: F401  (fixture)


def test_party_and_item_profit(co):
    with connect(co["path"]) as conn:
        pp = R.run(conn, WS, "party_pl", {})
        assert pp["rows"][0]["party"] == "Hotel A" and pp["rows"][0]["profit"] == 2000   # 5000 − 5×600
        ip = R.run(conn, WS, "item_pl", {})
        assert ip["rows"][0]["item"] == "Curtain" and ip["rows"][0]["qty"] == 5 and ip["rows"][0]["profit"] == 2000


def test_party_item_group_category(co):
    with connect(co["path"]) as conn:
        pi = R.run(conn, WS, "party_item", {"party_id": co["cust"]["id"]})
        assert pi["rows"][0]["item"] == "Curtain" and pi["rows"][0]["sold_qty"] == 5
        gs = R.run(conn, WS, "group_sale", {})
        assert gs["total"] == 5900 and gs["rows"][0]["bills"] == 1
        cr = R.run(conn, WS, "category_report", {})
        assert cr["rows"][0]["sale_qty"] == 5 and cr["rows"][0]["purchase_qty"] == 10


def test_discount_orders_expense_items_gstr9(co):
    with connect(co["path"]) as conn:
        create(conn, WS, {"txn_type": 1, "customer_id": co["cust"]["id"], "txn_date": "2026-11-20",
                          "lines": [{"item_name": "Cushion", "qty": 2, "rate": 500, "discount_pct": 10}], "discount_amount": 50})
        d = R.run(conn, WS, "discount_report", {})
        assert d["discount_given"] == 150      # 10% of 1000 on the item + 50 on the bill
        so = create(conn, WS, {"txn_type": 65, "customer_id": co["cust"]["id"], "lines": [{"item_name": "x", "qty": 1, "rate": 700}]})
        o = R.run(conn, WS, "sale_orders", {})
        assert o["open_count"] == 1 and o["open_value"] == 700
        assert R.run(conn, WS, "expense_items", {})["total"] == 4000
        g9 = R.run(conn, WS, "gstr9", {"from": "2026-04-01", "to": "2027-03-31"})
        assert g9["b2b_invoices"] >= 1 and g9["table4_outward"]["cgst"] >= 450
        assert so["party_txn_id"]


def test_all_new_reports_listed():
    for k in ("party_pl", "item_pl", "party_item", "group_sale", "group_purchase", "category_report",
              "discount_report", "expense_items", "sale_orders", "purchase_orders", "gstr9"):
        assert k in R.REPORTS


def test_credit_limit_warning(co):
    from app import hop_settings

    with connect(co["path"]) as conn:
        conn.execute("UPDATE hop_customers SET credit_limit=3000, credit_no_limit=0 WHERE id=?", (co["cust"]["id"],))
        create(conn, WS, {"txn_type": 1, "customer_id": co["cust"]["id"], "lines": [{"item_name": "x", "qty": 1, "rate": 2500}]})
        w = hop_settings.invoice_entry_warnings(conn, WS, {"txn_type": 1, "party_type": "customer", "party_id": co["cust"]["id"],
                                                           "lines": [{"item_name": "y", "qty": 1, "rate": 1000}]})
        assert any(x["code"] == "credit_limit" for x in w)
        ok = hop_settings.invoice_entry_warnings(conn, WS, {"txn_type": 1, "party_type": "customer", "party_id": co["cust"]["id"],
                                                            "received_amount": 1000,
                                                            "lines": [{"item_name": "y", "qty": 1, "rate": 400}]})
        assert not any(x["code"] == "credit_limit" for x in ok)

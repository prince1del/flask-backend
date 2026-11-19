"""Distributor UI on a made-up distributor firm: order to the company → SO → CI, ledger, retailers, stock, forecast."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from app import hop_distributor as D
from app import hop_money
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_ops import create_product, create_vendor
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


def _d(days_ago: int) -> str:
    return (D._today() - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def firm(tmp_path: Path):
    path = str(tmp_path / "dist.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        D.ensure_schema(conn)
        brand = create_vendor(conn, WS, {"company": "Brand Mills", "lead_time_days": 10})
        other = create_vendor(conn, WS, {"company": "Packing Co"})
        D.set_principal(conn, WS, brand["id"], True)
        towel = create_product(conn, WS, {"name": "Towel", "code": "T1", "stock_qty": 0, "purchase_price": 100,
                                          "selling_price": 150, "gst_pct": 5})
        sheet = create_product(conn, WS, {"name": "Bedsheet", "code": "B1", "stock_qty": 0, "purchase_price": 400,
                                          "selling_price": 600, "gst_pct": 5})
        r1 = create_customer(conn, WS, {"company": "Sharma Store"})
        r2 = create_customer(conn, WS, {"company": "Gupta Mart"})
        # my orders to the company
        po1 = create(conn, WS, {"txn_type": 66, "party_type": "vendor", "vendor_id": brand["id"], "txn_date": _d(40),
                                "lines": [{"item_name": "Towel", "product_id": towel["id"], "qty": 100, "rate": 100, "tax_pct": 5}]})
        po2 = create(conn, WS, {"txn_type": 66, "party_type": "vendor", "vendor_id": brand["id"], "txn_date": _d(9),
                                "lines": [{"item_name": "Bedsheet", "product_id": sheet["id"], "qty": 20, "rate": 400, "tax_pct": 5}]})
        po3 = create(conn, WS, {"txn_type": 66, "party_type": "vendor", "vendor_id": brand["id"], "txn_date": _d(2),
                                "lines": [{"item_name": "Towel", "product_id": towel["id"], "qty": 50, "rate": 100, "tax_pct": 5}]})
        create(conn, WS, {"txn_type": 66, "party_type": "vendor", "vendor_id": other["id"], "txn_date": _d(3),
                          "lines": [{"item_name": "Box", "qty": 10, "rate": 5}]})
        so1 = D.create_so(conn, WS, {"po_txn_id": po1["party_txn_id"], "so_number": "SO-1001", "so_date": _d(38),
                                     "lines": [{"item_name": "Towel", "qty": 100, "rate": 100}]})
        D.create_so(conn, WS, {"po_txn_id": po2["party_txn_id"], "so_number": "SO-1002", "so_date": _d(8)})
        # company's invoice (CI) for the first order, converted from the order
        ci = create(conn, WS, {"txn_type": 2, "party_type": "vendor", "vendor_id": brand["id"], "txn_date": _d(35),
                               "party_bill_no": "CI-77", "linked_from_txn_id": po1["party_txn_id"], "due_date": _d(5),
                               "lines": [{"item_name": "Towel", "product_id": towel["id"], "qty": 100, "rate": 100, "tax_pct": 5}]})
        D.link_bill(conn, WS, so1["id"], ci["party_txn_id"])
        create(conn, WS, {"txn_type": 4, "party_type": "vendor", "vendor_id": brand["id"], "txn_date": _d(20), "amount": 4000,
                          "payment_mode": "cash", "allocations": [{"txn_id": ci["party_txn_id"], "amount": 4000}]})
        # retailers: 60 towels in 30 days
        create(conn, WS, {"txn_type": 1, "customer_id": r1["id"], "txn_date": _d(25),
                          "lines": [{"item_name": "Towel", "product_id": towel["id"], "qty": 30, "rate": 150, "tax_pct": 5}]})
        create(conn, WS, {"txn_type": 1, "customer_id": r2["id"], "txn_date": _d(5),
                          "lines": [{"item_name": "Towel", "product_id": towel["id"], "qty": 20, "rate": 150, "tax_pct": 5}]})
        create(conn, WS, {"txn_type": 30, "customer_id": r1["id"], "txn_date": _d(3),
                          "lines": [{"item_name": "Towel", "product_id": towel["id"], "qty": 10, "rate": 150}]})
    return path, {"brand": brand["id"], "po1": po1["party_txn_id"], "po2": po2["party_txn_id"], "po3": po3["party_txn_id"],
                  "ci": ci["party_txn_id"], "r1": r1["id"], "towel": towel["id"]}


def test_pipeline_status_per_order(firm):
    path, ids = firm
    with connect(path) as conn:
        pl = D.pipeline(conn, WS)
        st = {o["id"]: o["status"] for o in pl["orders"]}
        assert st == {ids["po1"]: "Billed", ids["po2"]: "SO received", ids["po3"]: "Ordered"}   # Packing Co not a principal
        po1 = next(o for o in pl["orders"] if o["id"] == ids["po1"])
        assert po1["bills"][0]["party_bill_no"] == "CI-77" and po1["sos"][0]["so_number"] == "SO-1001"
        with pytest.raises(ValueError):
            D.create_so(conn, WS, {"so_number": "so-1001"})           # same SO twice
        with pytest.raises(ValueError):
            D.link_bill(conn, WS, po1["sos"][0]["id"], ids["po3"])    # an order is not a company invoice


def test_company_ledger_and_payment_status(firm):
    path, ids = firm
    with connect(path) as conn:
        lg = D.company_ledger(conn, WS, ids["brand"])
        s = lg["summary"]
        assert s["billed"] == 10500 and s["paid"] == 4000 and s["to_pay"] == 6500
        assert s["overdue"] == 6500 and lg["open_bills"][0]["overdue_days"] == 5
        assert lg["lines"] and lg["lines"][-1]["balance"] in (-6500, 6500)


def test_retailer_goods_counts_challans_as_goods_not_money(firm):
    path, ids = firm
    with connect(path) as conn:
        rg = D.retailer_goods(conn, WS)
        sharma = next(r for r in rg["retailers"] if r["party"] == "Sharma Store")
        assert sharma["qty"] == 40 and sharma["amount"] == 4725 and sharma["bills"] == 2
        assert sharma["items"][0]["item"] == "Towel"
        only = D.retailer_goods(conn, WS, party_id=ids["r1"])
        assert [r["party"] for r in only["retailers"]] == ["Sharma Store"]


def test_forecast_and_home(firm):
    path, ids = firm
    with connect(path) as conn:
        fc = D.forecast(conn, WS, lookback_days=30, cover_days=30)
        towel = next(i for i in fc["items"] if i["item"] == "Towel")
        # bought 100, sold 50 on invoices + 10 on a challan; 50 more on order (po3)
        assert towel["sold"] == 60 and towel["per_day"] == 2.0 and towel["on_order"] == 50
        assert towel["lead_days"] == 10 and towel["order_qty"] == max(0, 2 * 40 - towel["stock"] - 50)
        h = D.home(conn, WS)
        assert h["orders_open"] == 2 and h["orders_waiting_so"] == 1 and h["so_waiting_ci"] == 1
        assert h["to_pay_company"] == 6500 and h["overdue_company"] == 6500
        assert h["top_retailers"][0]["party"] in ("Sharma Store", "Gupta Mart")
        assert any("overdue" in a for a in h["alerts"])


def test_no_principal_hint(tmp_path):
    path = str(tmp_path / "empty.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        h = D.home(conn, WS)
        assert h["orders_open"] == 0 and "Set company" in h["alerts"][0]


def test_ask_nexora_distributor_tools(firm):
    from app.services import hop_ai

    path, ids = firm
    with connect(path) as conn:
        o = hop_ai.books_answer(conn, WS, "dist_orders")
        assert {x["status"] for x in o["open_orders"]} == {"Ordered", "SO received"}
        lg = hop_ai.books_answer(conn, WS, "dist_company_ledger")
        assert lg["companies"][0]["company"] == "Brand Mills" and lg["companies"][0]["to_pay"] == 6500
        rg = hop_ai.books_answer(conn, WS, "dist_retailer_goods", party_name="sharma")
        assert [r["retailer"] for r in rg["retailers"]] == ["Sharma Store"]
        fc = hop_ai.books_answer(conn, WS, "dist_forecast")
        assert "order_now" in fc and all(i["flag"] != "OK" for i in fc["items"])
        wh = hop_ai.books_answer(conn, WS, "dist_warehouse", party_name="towel")
        assert wh["items"][0]["item"] == "Towel"
        ov = hop_ai.books_answer(conn, WS, "dist_overview")
        assert ov["companies"] == ["Brand Mills"] and "alerts" in ov


def test_distributor_prompt_only_in_distributor_ui():
    from flask import Flask

    from app.services import nexora_ai_agent as agent

    app = Flask("t")
    with app.test_request_context(headers={"X-Nexora-UI": "distributor"}):
        assert agent._distributor_ui() and agent._business_ui()
        assert "DISTRIBUTOR UI" in agent._system_prompt()
    with app.test_request_context(headers={"X-Nexora-UI": "business"}):
        assert not agent._distributor_ui() and "DISTRIBUTOR UI" not in agent._system_prompt()

"""Distributor part 2 on a made-up firm: Article Master, AI entry checks, ledger check, follow-ups, staff, profit."""
from __future__ import annotations

import io
from datetime import timedelta
from pathlib import Path

import pytest

from app import hop_dist_extra as X
from app import hop_distributor as D
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_ops import create_vendor
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


def _d(n: int) -> str:
    return (X._today() - timedelta(days=n)).isoformat()


@pytest.fixture
def firm(tmp_path: Path):
    path = str(tmp_path / "d2.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        D.ensure_schema(conn); X.ensure_schema(conn)
        brand = create_vendor(conn, WS, {"company": "Brand Mills"})
        D.set_principal(conn, WS, brand["id"], True)
        towel = X.save_article(conn, WS, {"code": "T-100", "name": "Bath Towel", "category": "Towels", "season": "AW26",
                                          "mrp": 300, "purchase_price": 150, "selling_price": 200, "gst_pct": 5})
        sheet = X.save_article(conn, WS, {"code": "B-200", "name": "Double Bedsheet", "category": "Sheets",
                                          "mrp": 1000, "purchase_price": 500, "selling_price": 650, "gst_pct": 5})
        shop = create_customer(conn, WS, {"company": "Sharma Store", "mobile": "9000000001"})
        po = create(conn, WS, {"txn_type": 66, "party_type": "vendor", "vendor_id": brand["id"], "txn_date": _d(20),
                               "lines": [{"item_name": "Bath Towel", "product_id": towel["id"], "qty": 100, "rate": 150, "tax_pct": 5}]})
        ci = create(conn, WS, {"txn_type": 2, "party_type": "vendor", "vendor_id": brand["id"], "txn_date": _d(15),
                               "party_bill_no": "CI-1", "linked_from_txn_id": po["party_txn_id"],
                               "lines": [{"item_name": "Bath Towel", "product_id": towel["id"], "qty": 100, "rate": 150, "tax_pct": 5}]})
        inv = create(conn, WS, {"txn_type": 1, "customer_id": shop["id"], "txn_date": _d(50), "due_date": _d(40),
                                "lines": [{"item_name": "Bath Towel", "product_id": towel["id"], "qty": 10, "rate": 200, "tax_pct": 5},
                                          {"item_name": "Double Bedsheet", "product_id": sheet["id"], "qty": 2, "rate": 480, "tax_pct": 5}]})
    return path, {"brand": brand["id"], "towel": towel["id"], "sheet": sheet["id"], "shop": shop["id"],
                  "po": po["party_txn_id"], "ci": ci["party_txn_id"], "inv": inv["party_txn_id"]}


def test_article_master_margins_history_and_problems(firm):
    path, ids = firm
    with connect(path) as conn:
        a = X.articles(conn, WS)
        towel = next(r for r in a["articles"] if r["code"] == "T-100")
        assert towel["margin"] == 50 and towel["margin_pct"] == 25.0 and towel["retailer_margin_pct"] == 33.3
        X.save_article(conn, WS, {"id": ids["towel"], "purchase_price": 160}, user="Owner")
        h = X.price_history(conn, WS, ids["towel"])
        assert h[0]["field"] == "purchase_price" and h[0]["old_value"] == 150 and h[0]["new_value"] == 160
        with pytest.raises(ValueError):
            X.save_article(conn, WS, {"code": "t-100", "name": "Copy"})
        X.save_article(conn, WS, {"name": "No Price Pillow"})
        assert any("no buying price" in p for p in X.articles(conn, WS)["problems"])


def test_price_list_preview_and_apply(firm):
    path, ids = firm
    with connect(path) as conn:
        pv = X.price_list_preview(conn, WS, [
            {"code": "T-100", "name": "Bath Towel", "mrp": 320, "purchase_price": 150},     # MRP change
            {"code": "B-200", "name": "Double Bedsheet", "purchase_price": 900},             # +80% → check
            {"code": "", "name": "Hand Towel", "mrp": 120, "purchase_price": 60}])           # new
        acts = {r["name"]: r for r in pv["rows"]}
        assert acts["Bath Towel"]["action"] == "change" and acts["Hand Towel"]["action"] == "new"
        assert acts["Double Bedsheet"]["check"] and "up" in acts["Double Bedsheet"]["check"]
        done = X.price_list_apply(conn, WS, [acts["Bath Towel"], acts["Hand Towel"]], user="Owner")
        assert done == {"created": 1, "updated": 1, "margins": 0}
        assert X.price_history(conn, WS, ids["towel"])[0]["source"] == "price_list"


def test_price_list_excel_without_ai():
    from openpyxl import Workbook

    wb = Workbook(); ws = wb.active
    ws.append(["Company price list AW26"]); ws.append([])
    ws.append(["Art No", "Article Description", "MRP", "DP", "Category"])
    ws.append(["T-100", "Bath Towel", 300, 150, "Towels"]); ws.append([None, None, None, None, None])
    buf = io.BytesIO(); wb.save(buf)
    rows = X.read_price_list_excel(buf.getvalue())
    assert rows == [{"code": "T-100", "name": "Bath Towel", "mrp": 300.0, "purchase_price": 150.0, "category": "Towels"}]


def test_so_and_ci_checks_catch_wrong_data(firm):
    path, ids = firm
    with connect(path) as conn:
        ok = X.check_so(conn, WS, {"po_txn_id": ids["po"], "so_number": "SO-9", "so_date": _d(18),
                                   "lines": [{"item_name": "Bath Towel", "qty": 100, "rate": 150}]})
        assert ok["warnings"] == []
        bad = X.check_so(conn, WS, {"po_txn_id": ids["po"], "so_number": "SO-9", "so_date": _d(25), "amount": 99999,
                                    "lines": [{"item_name": "Bath Towel", "qty": 120, "rate": 170},
                                              {"item_name": "Unknown Rug", "qty": 1, "rate": 10}]})["warnings"]
        text = " ".join(bad)
        assert "before my order" in text and "only 100" in text and "Article Master says" in text
        assert "not in your Article Master" in text and "SO amount" in text
        ci = X.check_ci(conn, WS, {"txn_type": 2, "party_type": "vendor", "party_id": ids["brand"], "party_bill_no": "CI-1",
                                   "linked_from_txn_id": ids["po"],
                                   "lines": [{"item_name": "Bath Towel", "qty": 100, "rate": 150, "tax_pct": 12}]})
        assert any("duplicate" in w for w in ci) and any("GST 12%" in w for w in ci)
        assert X.check_ci(conn, WS, {"txn_type": 2, "party_type": "vendor", "party_id": 999, "lines": []}) == []


def test_company_ledger_check(firm):
    path, ids = firm
    with connect(path) as conn:
        res = X.ledger_check(conn, WS, ids["brand"], [
            {"date": _d(15), "doc_no": "CI-1", "debit": 15750, "credit": 0},
            {"date": _d(5), "doc_no": "CI-2", "debit": 4000, "credit": 0},
            {"date": _d(3), "doc_no": "UTR77", "debit": 0, "credit": 5000}])
        assert res["matched_bills"] == 1 and not res["ok"]
        assert res["in_company_ledger_not_in_my_books"]["invoices"][0]["doc_no"] == "CI-2"
        assert res["in_company_ledger_not_in_my_books"]["payments_or_credits"][0]["amount"] == 5000


def test_retailer_followup_promises_and_priority(firm):
    path, ids = firm
    with connect(path) as conn:
        d = X.retailer_dues(conn, WS)
        r = d["retailers"][0]
        assert r["party"] == "Sharma Store" and r["overdue"] > 0 and r["oldest_days"] == 40 and "40 days" in r["why"]
        assert X._inr(163725) == "₹1,63,725" and X._inr(999) == "₹999"
        assert d["call_today"][0]["party"] == "Sharma Store" and "never followed up" in r["why"]
        X.add_followup(conn, WS, {"party_id": ids["shop"], "note": "Will pay Friday", "promise_date": _d(2),
                                  "promise_amount": 1000, "at": _d(6)}, user="Ravi")
        det = X.retailer_detail(conn, WS, ids["shop"])
        assert det["followups"][0]["promise_state"] == "broken" and det["summary"]["broken_promises"] == 1
        assert "Sharma Store" in det["reminder_text"]
        with pytest.raises(ValueError):
            X.add_followup(conn, WS, {"party_id": ids["shop"]})


def test_staff_and_profit(firm):
    path, ids = firm
    with connect(path) as conn:
        from app import hop_audit

        hop_audit.ensure_schema(conn) if hasattr(hop_audit, "ensure_schema") else None
        conn.execute("INSERT INTO hop_audit_log (workspace_id, at, user_name, action, entity, entity_id, path) VALUES (?,?,?,?,?,?,?)",
                     (WS, X._today().isoformat() + "T10:00:00", "Ravi", "created", "Bill", str(ids["inv"]), "/api/v1/hop/party-transactions"))
        X.add_followup(conn, WS, {"party_id": ids["shop"], "note": "Called"}, user="Ravi")
        st = X.staff_performance(conn, WS, X._today().replace(day=1).isoformat(), X._today().isoformat())
        ravi = next(s for s in st["staff"] if s["staff"] == "Ravi")
        assert ravi["sale_bills"] == 1 and ravi["sales"] > 0 and ravi["followups"] == 1
        pf = X.profitability(conn, WS, _d(400), X._today().isoformat())
        t = pf["totals"]
        assert t["sales"] == 2960 and t["cost"] == 2500 and t["gross_profit"] == 460
        assert pf["sold_below_cost"][0]["item"] == "Double Bedsheet"
        assert pf["companies"][0]["company"] == "Brand Mills"


def test_ask_nexora_part2_tools(firm):
    from app.services import hop_ai

    path, ids = firm
    with connect(path) as conn:
        a = hop_ai.books_answer(conn, WS, "dist_articles", party_name="T-100")
        assert a["articles"][0]["code"] == "T-100"
        d = hop_ai.books_answer(conn, WS, "dist_retailer_dues", party_name="sharma")
        assert d["retailers"][0]["party"] == "Sharma Store" and d["call_today"]
        p = hop_ai.books_answer(conn, WS, "dist_profit")
        assert p["totals"]["gross_profit"] == 460
        s = hop_ai.books_answer(conn, WS, "dist_staff")
        assert "staff" in s


def test_price_changes_effect_and_margin_alert(firm):
    path, ids = firm
    with connect(path) as conn:
        conn.execute("UPDATE hop_products SET stock_qty=90 WHERE id=?", (ids["towel"],))
        X.save_article(conn, WS, {"id": ids["towel"], "purchase_price": 165}, user="Owner")   # DP 150 -> 165 (+10%)
        X.save_article(conn, WS, {"id": ids["sheet"], "mrp": 950}, user="Owner")             # MRP 1000 -> 950
        pc = X.price_changes(conn, WS, 30)
        towel = next(c for c in pc["changes"] if c["product_id"] == ids["towel"] and c["field"] == "purchase_price"
                     and not c["first_price"])
        assert towel["old"] == 150 and towel["new"] == 165 and towel["pct"] == 10.0 and towel["stock_effect"] == 1350
        assert pc["ups"] >= 1 and pc["downs"] >= 1 and pc["stock_value_effect"] == 1350
        al = next(a for a in pc["margin_alerts"] if a["product_id"] == ids["towel"])
        # sold at 200: margin 25% -> 17.5%; keeping 25% needs 165 / 0.75 = 220
        assert al["old_margin_pct"] == 25.0 and al["new_margin_pct"] == 17.5 and al["suggested_selling_price"] == 220
        X.save_article(conn, WS, {"id": ids["towel"], "selling_price": 220})
        assert not [a for a in X.price_changes(conn, WS, 30)["margin_alerts"] if a["product_id"] == ids["towel"]]
        art = next(a for a in X.articles(conn, WS)["articles"] if a["id"] == ids["towel"])
        assert art["last_change"]["field"] == "selling_price" and art["last_change"]["pct"] == 10.0
        assert X.articles(conn, WS)["recent_changes"]["articles_changed"] == 2


def test_category_gst_default_never_overwrites_a_set_gst(tmp_path):
    from app import hop_dist_extra as X
    from app.hop_db import connect
    from app.hop_schema import HOP_WORKSPACE_ID as W, ensure_hop_schema

    path = str(tmp_path / "g.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        X.ensure_schema(conn)
        X.set_category_default(conn, W, "Towel", 5)
        a = X.save_article(conn, W, {"name": "Flora 40x60", "category": "Towel", "mrp": 150})
        assert a["gst_pct"] == 5                                  # empty → category default
        X.save_article(conn, W, {"id": a["id"], "gst_pct": 12})
        b = X.save_article(conn, W, {"id": a["id"], "mrp": 160})  # price update without GST
        assert b["gst_pct"] == 12                                 # the set GST stays


def test_category_gst_screen_fill_only_empty(firm):
    path, ids = firm
    with connect(path) as conn:
        X.save_article(conn, WS, {"name": "Hand Towel", "category": "Towels", "mrp": 100, "purchase_price": 50})
        X.save_article(conn, WS, {"name": "Face Towel", "category": "Towels", "gst_pct": 12})
        o = X.category_gst_overview(conn, WS)
        towels = next(c for c in o["categories"] if c["category"] == "Towels")
        assert towels["articles"] == 3 and towels["no_gst"] == 1 and towels["mixed"]
        assert towels["suggested_gst"] == 5           # most towels already carry 5%
        with pytest.raises(ValueError):
            X.fill_category_gst(conn, WS, "Towels")     # no default yet
        with pytest.raises(ValueError):
            X.set_category_default(conn, WS, "Towels", 7)
        X.set_category_default(conn, WS, "Towels", 5)
        r = X.fill_category_gst(conn, WS, "Towels")
        assert r["filled"] == 1
        gst = {n: g for n, g in conn.execute("SELECT name, gst_pct FROM hop_products WHERE category='Towels'")}
        assert gst["Hand Towel"] == 5 and gst["Face Towel"] == 12 and gst["Bath Towel"] == 5   # set GST never changed

"""HoP Phase 2 — double-entry ledger and every report, on a small made-up company.

Story (all numbers are easy to check by hand):
* Opening: bank ₹50,000; Curtain 10 pcs @ ₹600 cost (₹6,000 stock); customer A owes ₹1,000 from before.
* Buy 10 Curtains from Mill @ ₹600 + 5% GST = ₹6,300, pay ₹3,000 by bank now.
* Sell 5 Curtains to Hotel A @ ₹1,000 + 18% GST = ₹5,900; Hotel A pays ₹2,000 cash now,
  later Payment-In ₹3,900 to bank against it.
* Rent ₹4,000 from bank. Owner adds ₹10,000 capital (bank deposit).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_books, hop_money, hop_reports as R
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create, delete_manual_party_document as delete
from app.hop_ops import create_product, create_vendor
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def co(tmp_path: Path):
    path = str(tmp_path / "hop_reports.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        conn.execute("INSERT OR REPLACE INTO hop_firm_profile (workspace_id, firm_name, gstin, state, updated_at) "
                     "VALUES (?, 'Demo Co', '07ABCDE1234F1Z5', 'Delhi', datetime('now'))", (WS,))
        bank = hop_books.create_bank_account(conn, WS, {"name": "Demo Bank", "opening_balance": 50000})
        prod = create_product(conn, WS, {"name": "Curtain", "code": "C1", "stock_qty": 10, "purchase_price": 600,
                                         "selling_price": 1000, "hsn": "6303", "gst_pct": 18})
        cust = create_customer(conn, WS, {"company": "Hotel A", "gst_no": "07AAAAA0000A1Z1"})
        conn.execute("UPDATE hop_customers SET opening_balance=1000 WHERE id=?", (cust["id"],))
        ven = create_vendor(conn, WS, {"company": "Mill", "gst_no": "27BBBBB1111B1Z2"})
        create(conn, WS, {"txn_type": 2, "party_type": "vendor", "vendor_id": ven["id"], "txn_date": "2026-11-02",
                          "lines": [{"item_name": "Curtain", "qty": 10, "rate": 600, "tax_pct": 5, "hsn": "6303"}],
                          "received_amount": 3000, "payment_mode": "bank", "payment_account_id": bank["id"]})
        inv = create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "txn_date": "2026-11-05",
                                "lines": [{"item_name": "Curtain", "qty": 5, "rate": 1000, "tax_pct": 18, "hsn": "6303"}],
                                "received_amount": 2000, "payment_mode": "cash"})
        create(conn, WS, {"txn_type": 3, "customer_id": cust["id"], "txn_date": "2026-11-10", "amount": 3900,
                          "payment_mode": "bank", "payment_account_id": bank["id"],
                          "allocations": [{"txn_id": inv["party_txn_id"], "amount": 3900}]})
        rent = next(c for c in hop_money.list_expense_categories(conn, WS) if c["name"] == "Rent")
        create(conn, WS, {"txn_type": 7, "expense_category_id": rent["id"], "txn_date": "2026-11-12", "amount": 4000,
                          "payment_mode": "bank", "payment_account_id": bank["id"]})
        hop_books.add_bank_entry(conn, WS, {"account_id": bank["id"], "entry_type": "in", "amount": 10000,
                                            "entry_date": "2026-11-13", "notes": "Capital added"})
        conn.commit()
    return {"path": path, "bank": bank, "prod": prod, "cust": cust, "ven": ven, "inv": inv}


def test_trial_balance_and_balance_sheet_balance(co):
    with connect(co["path"]) as conn:
        tb = R.trial_balance(conn, WS)
        assert tb["balanced"], tb
        bs = R.balance_sheet(conn, WS)
        assert bs["balanced"], bs
        assets = {a["name"]: a["amount"] for a in bs["assets"]}
        # bank 50000 − 3000 + 3900 − 4000 + 10000 = 56900; cash 2000
        assert assets["Demo Bank"] == 56900 and assets["Cash in hand"] == 2000
        assert assets["Sundry Debtors (to receive)"] == 1000          # only the opening balance is left
        assert assets["Closing stock"] == 9000                       # 10 + 10 − 5 = 15 pcs × 600
        liab = {x["name"]: x["amount"] for x in bs["liabilities"]}
        assert liab["Sundry Creditors (to pay)"] == 3300             # 6300 − 3000
        assert liab["GST payable"] == 600                            # 900 output − 300 input


def test_profit_and_loss(co):
    with connect(co["path"]) as conn:
        pl = R.profit_and_loss(conn, WS)
        assert (pl["net_sales"], pl["net_purchases"]) == (5000, 6000)
        assert (pl["opening_stock"], pl["closing_stock"]) == (6000, 9000)
        assert pl["gross_profit"] == 2000                            # 5000 − (6000 + 6000 − 9000)
        assert pl["total_expenses"] == 4000 and pl["net_profit"] == -2000
        # The same profit shows in the balance sheet.
        eq = {x["name"]: x["amount"] for x in R.balance_sheet(conn, WS)["equity"]}
        assert eq["Profit till date"] == -2000 and eq["Capital added"] == 10000


def test_party_statement_and_outstanding(co):
    with connect(co["path"]) as conn:
        st = R.party_statement(conn, WS, "customer", co["cust"]["id"])
        assert st["opening_balance"] == 1000 and st["closing_balance"] == 1000
        assert [round(l["balance"]) for l in st["lines"]] == [6900, 4900, 1000]
        out = R.outstanding(conn, WS)
        assert out["total_to_receive"] == 1000 and out["total_to_pay"] == 3300
        # From a date: opening carries everything before it.
        st2 = R.party_statement(conn, WS, "customer", co["cust"]["id"], frm="2026-11-06")
        assert st2["opening_balance"] == 4900 and len(st2["lines"]) == 1


def test_cash_flow_and_day_book(co):
    with connect(co["path"]) as conn:
        cf = R.cash_flow(conn, WS, "2026-11-01", "2026-11-30")
        assert cf["opening_cash"] == 50000 and cf["closing_cash"] == 58900
        assert cf["total_in"] == 15900 and cf["total_out"] == 7000
        db = R.day_book(conn, WS, "2026-11-05")
        assert len(db["rows"]) == 1 and db["money_in"] == 2000


def test_sale_item_stock_profit_expense_reports(co):
    with connect(co["path"]) as conn:
        s = R.sale_purchase_report(conn, WS, "sale")
        assert (s["net_taxable"], s["net_gst"], s["net_total"]) == (5000, 900, 5900)
        it = R.item_report(conn, WS)["rows"][0]
        assert (it["sale_qty"], it["purchase_qty"]) == (5, 10)
        ss = R.stock_summary(conn, WS)
        assert ss["rows"][0]["qty"] == 15 and ss["total_value"] == 9000
        mv = R.stock_movement(conn, WS, co["prod"]["id"])
        assert mv["opening_qty"] == 10 and [l["balance"] for l in mv["lines"]] == [20, 15]
        bp = R.bill_wise_profit(conn, WS)
        assert bp["total_profit"] == 2000                             # 5000 − 5 × 600
        ex = R.expense_report(conn, WS)
        assert ex["by_category"] == [{"category": "Rent", "amount": 4000}]


def test_gst_reports(co):
    with connect(co["path"]) as conn:
        g1 = R.gstr1(conn, WS)
        assert len(g1["b2b"]) == 1 and g1["b2b"][0]["cgst"] == 450 and g1["b2b"][0]["igst"] == 0   # Delhi → Delhi
        g2 = R.gstr2(conn, WS)
        assert g2["with_gstin"][0]["igst"] == 300                    # Maharashtra vendor → IGST
        g3 = R.gstr3b(conn, WS)
        assert g3["outward_supplies"]["cgst"] == 450 and g3["eligible_itc"]["igst"] == 300
        h = R.hsn_summary(conn, WS, "sale")
        assert h["rows"] == [{"hsn": "6303", "rate": 18.0, "qty": 5.0, "taxable": 5000.0, "tax": 900.0}]


def test_reports_stay_right_after_delete(co):
    with connect(co["path"]) as conn:
        delete(conn, WS, co["inv"]["party_txn_id"])
        assert R.trial_balance(conn, WS)["balanced"] and R.balance_sheet(conn, WS)["balanced"]
        assert R.profit_and_loss(conn, WS)["net_sales"] == 0


def test_imported_vyapar_docs_post_with_their_totals(co):
    with connect(co["path"]) as conn:
        conn.execute("""INSERT INTO hop_party_transactions (workspace_id, party_type, party_id, party_name, source_txn_id,
                        txn_type, txn_label, txn_number, txn_date, total_amount, balance_amount, status_text, created_at,
                        updated_at) VALUES (?, 'customer', ?, 'Hotel A', 901, 1, 'Sale Invoice', 'V-9', '2026-10-01',
                        1180, 180, 'Partial', datetime('now'), datetime('now'))""", (WS, co["cust"]["id"]))
        bs = R.balance_sheet(conn, WS)
        assert bs["balanced"]
        assets = {a["name"]: a["amount"] for a in bs["assets"]}
        assert assets["Cash & Bank (imported)"] == 1000 and assets["Sundry Debtors (to receive)"] == 1180


def test_run_dispatch_and_unknown(co):
    with connect(co["path"]) as conn:
        for key in R.REPORTS:
            args = {"party_id": co["cust"]["id"], "product_id": co["prod"]["id"]}
            assert isinstance(R.run(conn, WS, key, args), dict), key
        with pytest.raises(LookupError):
            R.run(conn, WS, "nope", {})


def test_books_routes(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    keys = [r["key"] for r in client.get("/api/v1/hop/books", headers=headers).get_json()["data"]["reports"]]
    assert "balance_sheet" in keys and "gstr3b" in keys
    for k in ("trial_balance", "profit_loss", "balance_sheet", "cash_flow", "day_book", "outstanding", "gstr1"):
        r = client.get(f"/api/v1/hop/books/{k}?from=2026-04-01&to=2027-03-31", headers=headers)
        assert r.status_code == 200, (k, r.get_json())
    assert client.get("/api/v1/hop/books/party_statement", headers=headers).status_code == 400
    assert client.get("/api/v1/hop/books/nope", headers=headers).status_code == 404
    assert client.get("/api/v1/hop/books/trial_balance").status_code == 401
    old = client.get("/api/v1/hop/reports/balance-sheet", headers=headers).get_json()["data"]
    assert old["balanced"] is True and "cash_bank" in old["assets"]


def test_ask_nexora_books_answers_reports(co):
    from app.services import hop_ai

    with connect(co["path"]) as conn:
        assert hop_ai.books_answer(conn, WS, "profit_loss")["net_profit"] == -2000
        st = hop_ai.books_answer(conn, WS, "party_statement", "hotel a")
        assert st["closing_balance"] == 1000
        assert "error" in hop_ai.books_answer(conn, WS, "party_statement", "")
        assert hop_ai.books_answer(conn, WS, "day_book", extra={"date": "2026-11-05"})["money_in"] == 2000
        # 450 CGST + 450 SGST out; 300 IGST credit used against CGST first → 150 + 450 = 600 (same as the balance sheet)
        g = hop_ai.books_answer(conn, WS, "gst")
        assert g["total_payable"] == 600 and g["tax_payable"] == {"igst": 0, "cgst": 150, "sgst": 450}

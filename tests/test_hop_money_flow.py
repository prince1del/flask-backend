"""HoP money flow, Vyapar-style: paid now, cash/bank/cheque, Payment-In/Out against
bills, stock from documents, expense categories, Convert — and edit/delete undo."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_books, hop_money
from app.hop_db import connect, create_customer
from app.hop_doc_preview import (
    create_manual_party_document as create,
    delete_manual_party_document as delete,
    get_party_transaction_edit_data,
    update_manual_party_document as update,
)
from app.hop_ops import create_product, create_vendor
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def db(tmp_path: Path):
    path = str(tmp_path / "hop_money.sqlite3")
    ensure_hop_schema(path)
    return path


def _acct(conn, name, kind="bank", opening=0):
    return hop_books.create_bank_account(conn, WS, {"name": name, "account_type": kind, "opening_balance": opening})


def _bal(conn, account_id):
    return float(conn.execute("SELECT current_balance FROM hop_bank_accounts WHERE id=?", (account_id,)).fetchone()[0])


def _txn(conn, txn_id):
    return dict(conn.execute("SELECT * FROM hop_party_transactions WHERE id=?", (txn_id,)).fetchone())


def _stock(conn, pid):
    return float(conn.execute("SELECT stock_qty FROM hop_products WHERE id=?", (pid,)).fetchone()[0])


LINE = {"item_name": "Sofa", "qty": 2, "unit": "NOS", "rate": 5000, "tax_pct": 0}


def test_invoice_received_now_goes_to_bank_and_sets_status(db):
    with connect(db) as conn:
        bank = _acct(conn, "HDFC", opening=1000)
        cust = create_customer(conn, WS, {"company": "Hotel A"})
        inv = create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "lines": [LINE],
                                "received_amount": 4000, "payment_mode": "bank", "payment_account_id": bank["id"]})
        t = _txn(conn, inv["party_txn_id"])
        assert (t["balance_amount"], t["status_text"]) == (6000, "Partial")
        assert _bal(conn, bank["id"]) == 5000

        # Edit: received more, paid in cash instead — bank goes back, cash goes up.
        update(conn, WS, inv["party_txn_id"], {"lines": [LINE], "received_amount": 10000, "payment_mode": "cash"})
        cash_id = hop_money.ensure_cash_account(conn, WS)
        assert _bal(conn, bank["id"]) == 1000 and _bal(conn, cash_id) == 10000
        assert _txn(conn, inv["party_txn_id"])["status_text"] == "Paid"

        delete(conn, WS, inv["party_txn_id"])
        assert _bal(conn, cash_id) == 0


def test_payment_in_clears_bills_and_keeps_unused(db):
    with connect(db) as conn:
        cust = create_customer(conn, WS, {"company": "Hotel B"})
        a = create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "txn_date": "2026-11-01", "lines": [LINE]})
        b = create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "txn_date": "2026-11-02", "lines": [LINE]})
        bills = hop_money.open_bills(conn, WS, 3, "customer", cust["id"])
        assert [x["due"] for x in bills] == [10000, 10000]

        pay = create(conn, WS, {"txn_type": 3, "customer_id": cust["id"], "amount": 15000, "payment_mode": "cash",
                                "allocations": [{"txn_id": a["party_txn_id"], "amount": 10000},
                                                {"txn_id": b["party_txn_id"], "amount": 4000}]})
        assert _txn(conn, a["party_txn_id"])["status_text"] == "Paid"
        assert (_txn(conn, b["party_txn_id"])["balance_amount"], _txn(conn, b["party_txn_id"])["status_text"]) == (6000, "Partial")
        p = _txn(conn, pay["party_txn_id"])
        assert (p["unused_amount"], p["status_text"]) == (1000, "Partly used")
        edit = get_party_transaction_edit_data(conn, WS, pay["party_txn_id"])
        assert {x["txn_id"]: x["amount"] for x in edit["allocations"]} == {a["party_txn_id"]: 10000, b["party_txn_id"]: 4000}

        # Too much on one bill is refused, nothing changes.
        with pytest.raises(ValueError):
            update(conn, WS, pay["party_txn_id"], {"amount": 15000, "allocations": [{"txn_id": b["party_txn_id"], "amount": 12000}]})
        conn.rollback()
        assert _txn(conn, b["party_txn_id"])["balance_amount"] == 6000

        # Deleting the payment puts both bills back to unpaid.
        delete(conn, WS, pay["party_txn_id"])
        assert _txn(conn, a["party_txn_id"])["balance_amount"] == 10000
        assert _txn(conn, b["party_txn_id"])["status_text"] == "Unpaid"


def test_payment_cannot_clear_another_partys_bill(db):
    with connect(db) as conn:
        c1 = create_customer(conn, WS, {"company": "Hotel C1"})
        c2 = create_customer(conn, WS, {"company": "Hotel C2"})
        inv = create(conn, WS, {"txn_type": 1, "customer_id": c1["id"], "lines": [LINE]})
        with pytest.raises(ValueError):
            create(conn, WS, {"txn_type": 3, "customer_id": c2["id"], "amount": 100,
                              "allocations": [{"txn_id": inv["party_txn_id"], "amount": 100}]})


def test_imported_bill_keeps_its_paid_part_when_a_payment_is_linked(db):
    with connect(db) as conn:
        cust = create_customer(conn, WS, {"company": "Old Party"})
        conn.execute("""INSERT INTO hop_party_transactions (workspace_id, party_type, party_id, party_name, source_txn_id,
                        txn_type, txn_label, txn_number, txn_date, total_amount, balance_amount, status_text,
                        created_at, updated_at) VALUES (?, 'customer', ?, 'Old Party', 501, 1, 'Sale Invoice', 'V-1',
                        '2026-04-01', 1000, 400, 'Partial', datetime('now'), datetime('now'))""", (WS, cust["id"]))
        old_id = conn.execute("SELECT id FROM hop_party_transactions WHERE source_txn_id=501").fetchone()[0]
        create(conn, WS, {"txn_type": 3, "customer_id": cust["id"], "amount": 400,
                          "allocations": [{"txn_id": old_id, "amount": 400}]})
        assert (_txn(conn, old_id)["balance_amount"], _txn(conn, old_id)["status_text"]) == (0, "Paid")


def test_stock_moves_with_sale_purchase_and_returns(db):
    with connect(db) as conn:
        prod = create_product(conn, WS, {"name": "Cushion", "code": "CU-1", "stock_qty": 10, "selling_price": 500})
        cust = create_customer(conn, WS, {"company": "Hotel D"})
        ven = create_vendor(conn, WS, {"company": "Mill E"})
        sale = create(conn, WS, {"txn_type": 1, "customer_id": cust["id"],
                                 "lines": [{"item_name": "Cushion", "qty": 3, "rate": 500}]})
        assert _stock(conn, prod["id"]) == 7
        create(conn, WS, {"txn_type": 2, "party_type": "vendor", "vendor_id": ven["id"],
                          "lines": [{"item_code": "CU-1", "item_name": "Cushion cover", "qty": 5, "rate": 300}]})
        assert _stock(conn, prod["id"]) == 12
        create(conn, WS, {"txn_type": 21, "customer_id": cust["id"],
                          "lines": [{"product_id": prod["id"], "item_name": "x", "qty": 1, "rate": 500}]})
        assert _stock(conn, prod["id"]) == 13
        update(conn, WS, sale["party_txn_id"], {"lines": [{"item_name": "Cushion", "qty": 1, "rate": 500}]})
        assert _stock(conn, prod["id"]) == 15
        delete(conn, WS, sale["party_txn_id"])
        assert _stock(conn, prod["id"]) == 16
        # Estimates never move stock; services (no product) are ignored.
        create(conn, WS, {"txn_type": 27, "customer_id": cust["id"], "lines": [{"item_name": "Cushion", "qty": 4, "rate": 1}]})
        create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "lines": [{"item_name": "Stitching", "qty": 4, "rate": 1}]})
        assert _stock(conn, prod["id"]) == 16


def test_expense_with_category_and_no_party(db):
    with connect(db) as conn:
        cats = hop_money.list_expense_categories(conn, WS)
        rent = next(c for c in cats if c["name"] == "Rent")
        bank = _acct(conn, "ICICI", opening=50000)
        exp = create(conn, WS, {"txn_type": 7, "expense_category_id": rent["id"], "amount": 20000,
                                "payment_mode": "bank", "payment_account_id": bank["id"]})
        t = _txn(conn, exp["party_txn_id"])
        assert (t["party_type"], t["party_name"], t["party_id"]) == ("expense", "Rent", None)
        assert _bal(conn, bank["id"]) == 30000
        assert next(c for c in hop_money.list_expense_categories(conn, WS) if c["name"] == "Rent")["total"] == 20000
        with pytest.raises(ValueError):
            create(conn, WS, {"txn_type": 7, "amount": 10})  # neither party nor category


def test_cheque_waits_until_deposited_and_locks_the_payment(db):
    with connect(db) as conn:
        bank = _acct(conn, "SBI")
        cust = create_customer(conn, WS, {"company": "Hotel F"})
        pay = create(conn, WS, {"txn_type": 3, "customer_id": cust["id"], "amount": 7000,
                                "payment_mode": "cheque", "payment_ref": "000123", "cheque_date": "2026-11-20"})
        assert _bal(conn, bank["id"]) == 0
        chq = hop_money.list_cheques(conn, WS, "open")
        assert len(chq) == 1 and chq[0]["cheque_no"] == "000123"
        hop_money.close_cheque(conn, WS, chq[0]["id"], bank["id"], "2026-11-21")
        assert _bal(conn, bank["id"]) == 7000
        with pytest.raises(ValueError):
            update(conn, WS, pay["party_txn_id"], {"amount": 5000, "payment_mode": "cheque"})
        conn.rollback()
        with pytest.raises(ValueError):
            delete(conn, WS, pay["party_txn_id"])
        conn.rollback()
        hop_money.reopen_cheque(conn, WS, chq[0]["id"])
        assert _bal(conn, bank["id"]) == 0
        delete(conn, WS, pay["party_txn_id"])
        assert hop_money.list_cheques(conn, WS) == []


def test_convert_estimate_to_invoice(db):
    with connect(db) as conn:
        cust = create_customer(conn, WS, {"company": "Hotel G"})
        est = create(conn, WS, {"txn_type": 27, "customer_id": cust["id"], "lines": [LINE], "notes": "Lobby"})
        with pytest.raises(ValueError):
            hop_money.convert_draft(conn, WS, est["party_txn_id"], 2)  # estimate → purchase bill: no
        draft = hop_money.convert_draft(conn, WS, est["party_txn_id"], 1)
        assert draft["lines"][0]["qty"] == 2 and draft["notes"] == "Lobby"
        inv = create(conn, WS, draft)
        assert _txn(conn, inv["party_txn_id"])["linked_from_txn_id"] == est["party_txn_id"]
        assert _txn(conn, est["party_txn_id"])["status_text"].startswith("Converted to Sale Invoice")
        with pytest.raises(ValueError):
            hop_money.convert_draft(conn, WS, est["party_txn_id"], 1)  # already converted
        delete(conn, WS, inv["party_txn_id"])
        assert _txn(conn, est["party_txn_id"])["status_text"] == "Open"


def test_bank_transfer_and_cash_account(db):
    with connect(db) as conn:
        accts = hop_books.list_bank_accounts(conn, WS)
        cash = next(a for a in accts if a["account_type"] == "cash")
        bank = _acct(conn, "Axis", opening=500)
        hop_books.add_bank_entry(conn, WS, {"account_id": bank["id"], "entry_type": "transfer", "amount": 200,
                                            "counterparty_account_id": cash["id"]})
        assert (_bal(conn, bank["id"]), _bal(conn, cash["id"])) == (300, 200)
        with pytest.raises(ValueError):
            hop_books.add_bank_entry(conn, WS, {"account_id": bank["id"], "entry_type": "transfer", "amount": 1})


def test_api_routes(tmp_path, monkeypatch):
    """Routes answer for a hop_admin and refuse others."""
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    cats = client.get("/api/v1/hop/expense-categories", headers=headers)
    assert cats.status_code == 200 and cats.get_json()["data"]["categories"]
    assert client.get("/api/v1/hop/cheques", headers=headers).status_code == 200
    assert client.get("/api/v1/hop/open-bills?payment_type=3&party_id=1", headers=headers).status_code == 200
    accts = client.get("/api/v1/hop/bank-accounts", headers=headers).get_json()["data"]["accounts"]
    assert any(a["account_type"] == "cash" for a in accts)


def test_ai_bill_is_matched_to_vendor_items_and_category(db):
    from app.services import hop_ai

    with connect(db) as conn:
        ven = create_vendor(conn, WS, {"company": "Shree Ganesh Fabrics"})
        prod = create_product(conn, WS, {"name": "Velvet Fabric", "code": "VF-1", "hsn": "5801"})
        out = hop_ai.match_bill(conn, WS, {
            "party": "SHREE GANESH FABRICS PVT", "bill_no": "SG/221", "bill_date": "2026-11-10",
            "lines": [{"item": "velvet fabric", "qty": 10, "unit": "MTR", "rate": 400, "tax_pct": 5},
                      {"item": "Courier", "qty": 1, "rate": 100, "tax_pct": 18}],
            "total": 4318, "expense_category": "",
        })
        assert out["vendor_id"] == ven["id"] and out["bill_no"] == "SG/221"
        assert out["lines"][0]["product_id"] == prod["id"] and out["lines"][0]["item_name"] == "Velvet Fabric"
        assert out["lines"][1]["product_id"] is None
        assert any("not in your catalogue" in c for c in out["checks"])

        exp = hop_ai.match_bill(conn, WS, {"party": "BSES", "total": 2500, "expense_category": "electricity"})
        assert exp["expense_category"] == "Electricity" and exp["vendor_id"] is None


def test_ai_books_answer_for_ask_nexora(db):
    from app.services import hop_ai

    with connect(db) as conn:
        cust = create_customer(conn, WS, {"company": "Grand Hotel"})
        create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "lines": [LINE], "received_amount": 1000,
                          "payment_mode": "cash"})
        rec = hop_ai.books_answer(conn, WS, "receivables")
        assert rec["total"] == 9000 and rec["who_owes_us"][0]["party_name"] == "Grand Hotel"
        cash = hop_ai.books_answer(conn, WS, "cash_bank")
        assert cash["total"] == 1000
        assert hop_ai.books_answer(conn, WS, "overview")["receivable_total"] == 9000

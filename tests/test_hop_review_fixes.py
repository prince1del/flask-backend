"""Fixes after the Phase 3 review: recycle bin, restore, loans and fixed assets in the ledger."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_books, hop_money, hop_phase3_ext as X, hop_reports as R
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create, delete_manual_party_document as delete
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema
from app.services import hop_ai


@pytest.fixture
def setup(tmp_path: Path):
    path = str(tmp_path / "review.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        bank = hop_books.create_bank_account(conn, WS, {"name": "B", "opening_balance": 10000})
        cust = create_customer(conn, WS, {"company": "Hotel Z"})
        inv = create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "lines": [{"item_name": "x", "qty": 1, "rate": 5000}],
                                "received_amount": 2000, "payment_mode": "bank", "payment_account_id": bank["id"]})
        conn.commit()
    return path, bank, cust, inv


def _bank(conn, name="B"):
    return next(a["current_balance"] for a in hop_books.list_bank_accounts(conn, WS) if a["name"] == name)


def test_deleted_document_counts_nowhere(setup):
    path, bank, cust, inv = setup
    with connect(path) as conn:
        delete(conn, WS, inv["party_txn_id"])
        assert hop_money.open_bills(conn, WS, 3, "customer", cust["id"]) == []
        assert hop_ai.books_answer(conn, WS, "receivables")["total"] == 0
        assert R.outstanding(conn, WS)["total_to_receive"] == 0
        with pytest.raises(ValueError):  # cannot link a payment to a bill in the recycle bin
            create(conn, WS, {"txn_type": 3, "customer_id": cust["id"], "amount": 100,
                              "allocations": [{"txn_id": inv["party_txn_id"], "amount": 100}]})


def test_restore_keeps_the_payment_received(setup):
    path, bank, cust, inv = setup
    with connect(path) as conn:
        delete(conn, WS, inv["party_txn_id"])
        assert _bank(conn) == 10000
        X.restore_document(conn, WS, inv["party_txn_id"])
        t = dict(conn.execute("SELECT * FROM hop_party_transactions WHERE id=?", (inv["party_txn_id"],)).fetchone())
        assert (t["received_amount"], t["payment_mode"], t["balance_amount"]) == (2000, "bank", 3000)
        assert _bank(conn) == 12000


def test_loans_keep_the_balance_sheet_balanced(setup):
    path, bank, cust, inv = setup
    with connect(path) as conn:
        loan = X.create_loan_account(conn, WS, {"name": "HDFC loan", "principal": 100000})
        X.add_loan_entry(conn, WS, loan["id"], {"entry_type": "disbursement", "amount": 50000, "bank_account_id": bank["id"]})
        X.add_loan_entry(conn, WS, loan["id"], {"entry_type": "emi", "amount": 5000, "bank_account_id": bank["id"]})
        X.add_loan_entry(conn, WS, loan["id"], {"entry_type": "interest", "amount": 800, "bank_account_id": bank["id"]})
        bs = R.balance_sheet(conn, WS)
        assert bs["balanced"], bs
        assert {x["name"]: x["amount"] for x in bs["liabilities"]}["Loan — HDFC loan"] == 145000
        assert _bank(conn) == 10000 + 2000 + 50000 - 5000 - 800
        assert any(e["category"] == "Loan interest & fees" and e["amount"] == 800
                   for e in R.profit_and_loss(conn, WS)["expenses"])
        # A loan entry without an account still moves money (cash in hand) and stays balanced.
        X.add_loan_entry(conn, WS, loan["id"], {"entry_type": "fee", "amount": 100})
        assert R.balance_sheet(conn, WS)["balanced"]


def test_fixed_assets_keep_the_balance_sheet_balanced(setup):
    path, bank, cust, inv = setup
    with connect(path) as conn:
        X.create_fixed_asset(conn, WS, {"name": "Car", "purchase_cost": 40000, "purchase_date": "2026-04-01",
                                        "life_months": 60, "paid_from_account_id": bank["id"]})
        X.create_fixed_asset(conn, WS, {"name": "Old laptop", "purchase_cost": 30000, "purchase_date": "2026-04-01",
                                        "life_months": 30})
        X.run_depreciation(conn, WS, "2026-11-30")
        bs = R.balance_sheet(conn, WS)
        assert bs["balanced"] and R.trial_balance(conn, WS)["balanced"]
        assets = {a["name"]: a["amount"] for a in bs["assets"]}
        assert assets["Fixed Assets (cost)"] == 70000 and assets["Less: Accumulated Depreciation"] == -1666.67
        assert _bank(conn) == 12000 - 40000
        assert any(e["category"] == "Depreciation" for e in R.profit_and_loss(conn, WS)["expenses"])

"""HoP Other Income (rent received, interest…): money in, P&L, report (made-up company)."""
from __future__ import annotations

from pathlib import Path

from app import hop_books, hop_money, hop_reports
from app.hop_db import connect
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


def test_other_income_flows(tmp_path: Path):
    p = str(tmp_path / "oi.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        inc = hop_money.list_expense_categories(conn, WS, "income")
        exp = hop_money.list_expense_categories(conn, WS)
        assert "Interest received" in [c["name"] for c in inc] and "Interest received" not in [c["name"] for c in exp]
        rent = hop_money.create_expense_category(conn, WS, "Godown rent", "income")
        cash = next(a for a in hop_books.list_bank_accounts(conn, WS) if a["account_type"] == "cash")
        before = float(cash["current_balance"] or 0)
        create(conn, WS, {"txn_type": 29, "expense_category_id": rent["id"], "amount": 5000, "txn_date": "2026-11-16",
                          "payment_mode": "cash", "payment_account_id": cash["id"]})
        cash2 = next(a for a in hop_books.list_bank_accounts(conn, WS) if a["id"] == cash["id"])
        assert float(cash2["current_balance"]) == before + 5000
        pl = hop_reports.profit_and_loss(conn, WS)
        assert pl["total_other_income"] == 5000 and pl["net_profit"] == 5000
        assert pl["other_income"][0]["category"] == "Godown rent"
        assert hop_reports.balance_sheet(conn, WS)["balanced"]
        r = hop_reports.run(conn, WS, "other_income", {})
        assert r["total"] == 5000
        assert [c for c in hop_money.list_expense_categories(conn, WS, "income") if c["name"] == "Godown rent"][0]["total"] == 5000


def test_ask_nexora_other_income(tmp_path: Path):
    from app.services import hop_ai

    p = str(tmp_path / "oi2.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        cat = hop_money.create_expense_category(conn, WS, "Interest received", "income")
        create(conn, WS, {"txn_type": 29, "expense_category_id": cat["id"], "amount": 120, "payment_mode": "cash"})
        assert hop_ai.books_answer(conn, WS, "other_income")["total"] == 120

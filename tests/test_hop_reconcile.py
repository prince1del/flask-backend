"""HoP bank reconciliation (made-up company and statement)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_books, hop_reconcile as R
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create, update_manual_party_document as update
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema

CSV = """Bank of Somewhere,,,,
Account statement,,,,
Txn Date,Narration,Withdrawal Amt.,Deposit Amt.,Balance
16/11/2026,NEFT HOTEL R,,1180.00,
17/11/2026,SMS CHARGES,17.70,,
18/11/2026,INT PD,,42.00,
"""


@pytest.fixture
def db(tmp_path: Path):
    p = str(tmp_path / "rec.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        cust = create_customer(conn, WS, {"company": "Hotel R"})
        bank = hop_books.create_bank_account(conn, WS, {"name": "Test Bank", "account_type": "bank", "opening_balance": 1000})
        conn.commit()
    return p, cust["id"], bank["id"]


def test_reconcile_flow(db):
    p, cust, bank = db
    with connect(p) as conn:
        inv = create(conn, WS, {"txn_type": 1, "customer_id": cust, "txn_date": "2026-11-15", "received_amount": 1180,
                                "payment_mode": "bank", "payment_account_id": bank,
                                "lines": [{"item_name": "x", "qty": 1, "rate": 1000, "tax_pct": 18}]})
        st = R.statement(conn, WS, bank)
        assert (st["book_balance"], st["cleared_balance"], st["uncleared_in"]) == (2180, 1000, 1180)
        rows = R.parse_csv(CSV)
        assert [r["amount"] for r in rows] == [1180, -17.7, 42]
        m = R.match_rows(conn, WS, bank, rows)
        assert len(m["matches"]) == 1 and len(m["not_in_books"]) == 2
        assert "bank charge" in m["not_in_books"][0]["hint"] and "Other Income" in m["not_in_books"][1]["hint"]
        R.mark(conn, WS, bank, [m["matches"][0]["entry_id"]], "2026-11-16")
        assert R.statement(conn, WS, bank)["cleared_balance"] == 2180
        # Editing the invoice keeps the tick.
        update(conn, WS, inv["party_txn_id"], {"txn_type": 1, "customer_id": cust, "txn_date": "2026-11-15",
                                               "notes": "edited", "lines": [{"item_name": "x", "qty": 1, "rate": 1000, "tax_pct": 18}]})
        assert R.statement(conn, WS, bank)["cleared_balance"] == 2180
        with pytest.raises(ValueError):
            R.mark(conn, WS, bank, [], "2026-11-16")


def test_reconcile_api(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    acc = client.post("/api/v1/hop/bank-accounts", headers=headers, json={"name": "B", "account_type": "bank"}).get_json()["data"]
    r = client.get(f"/api/v1/hop/bank-accounts/{acc['id']}/reconcile", headers=headers)
    assert r.status_code == 200 and r.get_json()["data"]["book_balance"] == 0
    import io
    r = client.post(f"/api/v1/hop/bank-accounts/{acc['id']}/reconcile/match", headers=headers,
                    data={"file": (io.BytesIO(CSV.encode()), "s.csv")}, content_type="multipart/form-data")
    assert r.status_code == 200 and len(r.get_json()["data"]["not_in_books"]) == 3

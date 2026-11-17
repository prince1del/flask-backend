"""Loyalty points: earn on sale invoices from the start date, use as a discount, edit/delete keep points right."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_app_settings as S, hop_loyalty as L
from app.hop_db import connect, create_customer
from app.hop_doc_preview import (build_txn_preview, create_manual_party_document as create,
                                 get_party_transaction_edit_data, update_manual_party_document as update)
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def db(tmp_path: Path):
    p = str(tmp_path / "loyal.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        cust = create_customer(conn, WS, {"company": "Hotel L"})
        conn.commit()
    return p, cust["id"]


def _inv(conn, cust, rate, **kw):
    return create(conn, WS, {"txn_type": 1, "customer_id": cust, "txn_date": "2026-11-17",
                             "lines": [{"item_name": "Lamp", "qty": 1, "rate": rate}], **kw})


def test_off_by_default_and_start_date(db):
    p, cust = db
    with connect(p) as conn:
        old = create(conn, WS, {"txn_type": 1, "customer_id": cust, "txn_date": "2020-01-01",
                                "lines": [{"item_name": "x", "qty": 1, "rate": 5000}]})
        assert L.balance(conn, WS, cust)["on"] is False
        with pytest.raises(ValueError):
            _inv(conn, cust, 100, redeem_points=5)
        S.save(conn, WS, {"loyalty_on": True})
        assert S.get_all(conn, WS)["loyalty_from"]  # stamped with today
        assert L.balance(conn, WS, cust)["earned"] == 0  # old bill gives nothing
        assert old


def test_earn_redeem_edit_delete(db):
    p, cust = db
    with connect(p) as conn:
        S.save(conn, WS, {"loyalty_on": True, "loyalty_from": "2026-11-01", "loyalty_earn_per": 100, "loyalty_point_value": 1})
        _inv(conn, cust, 2550)
        assert L.balance(conn, WS, cust)["left"] == 25
        with pytest.raises(ValueError):
            _inv(conn, cust, 100, redeem_points=30)
        b = _inv(conn, cust, 500, redeem_points=20, discount_amount=10)
        t = conn.execute("SELECT discount_amount, total_amount FROM hop_party_transactions WHERE id=?", (b["party_txn_id"],)).fetchone()
        assert (t[0], t[1]) == (30, 470)  # ₹10 discount + 20 points
        assert L.balance(conn, WS, cust)["left"] == 25 + 4 - 20
        ed = get_party_transaction_edit_data(conn, WS, b["party_txn_id"])
        assert (ed["discount_amount"], ed["redeem_points"]) == (10, 20)
        assert "20 loyalty points" in build_txn_preview(conn, WS, party_txn_id=b["party_txn_id"])["loyalty_note"]
        # An edit form that does not know points keeps them.
        update(conn, WS, b["party_txn_id"], {"lines": [{"item_name": "Lamp", "qty": 1, "rate": 600}], "discount_amount": 10})
        t = conn.execute("SELECT discount_amount, total_amount FROM hop_party_transactions WHERE id=?", (b["party_txn_id"],)).fetchone()
        assert (t[0], t[1]) == (30, 570)
        # Editing with 0 points gives them back.
        update(conn, WS, b["party_txn_id"], {"lines": [{"item_name": "Lamp", "qty": 1, "rate": 600}], "redeem_points": 0})
        assert L.used_on_bill(conn, WS, b["party_txn_id"]) is None
        assert L.balance(conn, WS, cust)["left"] == 25 + 6
        # Deleted bill → its points no longer count.
        conn.execute("UPDATE hop_party_transactions SET deleted_at='2026-11-17' WHERE id=?", (b["party_txn_id"],))
        assert L.balance(conn, WS, cust)["left"] == 25


def test_ask_nexora_list(db):
    p, cust = db
    from app.services.hop_ai import books_answer

    with connect(p) as conn:
        S.save(conn, WS, {"loyalty_on": True, "loyalty_from": "2026-11-01"})
        _inv(conn, cust, 1000)
        out = books_answer(conn, WS, "loyalty", "hotel")
        assert out["customers"][0]["left"] == 10 and out["customers"][0]["customer"] == "Hotel L"

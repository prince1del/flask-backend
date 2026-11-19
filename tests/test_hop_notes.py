"""Business notes: add / edit / search / delete, party link, Ask Nexora action (made-up company)."""
from __future__ import annotations

import pytest

from app import hop_notes as N
from app.hop_db import connect
from app.hop_schema import HOP_WORKSPACE_ID as WS
from tests.test_hop_reports import co  # noqa: F401  (fixture)


def test_notes_crud_search(co):
    with connect(co["path"]) as conn:
        n = N.create(conn, WS, {"text": "Send new rates on Friday", "title": "Rates", "party_name": "hotel"})
        assert n["party_name"] == "Hotel A" and n["party_type"] == "customer"
        assert N.search(conn, WS, "friday")[0]["id"] == n["id"]
        assert N.search(conn, WS, party_name="Hotel")[0]["id"] == n["id"]
        u = N.update(conn, WS, n["id"], {"pinned": True, "text": "Send new rates on Saturday"})
        assert u["pinned"] == 1 and "Saturday" in u["body"]
        N.delete(conn, WS, n["id"])
        assert not N.search(conn, WS, "rates")
        with pytest.raises(ValueError):
            N.create(conn, WS, {"text": "  "})


def test_add_note_action_builds_request():
    from app.services.nexora_actions import build_request
    m, path, body = build_request("add_business_note", {"text": "Call Sharma", "source": "ask_nexora"})
    assert m == "POST" and path == "/api/v1/hop/notes" and body["text"] == "Call Sharma"


def test_note_comments(co):
    with connect(co["path"]) as conn:
        n = N.create(conn, WS, {"text": "link payment", "party_name": "claude"})
        r = N.add_comment(conn, WS, n["id"], {"by": "Claude", "text": "Done: …", "kind": "comment"})
        r = N.add_comment(conn, WS, n["id"], {"by": "Claude", "text": "Plan: 1 2 3", "kind": "plan"})
        assert [c["kind"] for c in r["comments"]] == ["comment", "plan"] and r["comments"][0]["by"] == "Claude"
        assert N.search(conn, WS, "link")[0]["comments"]


def test_link_earlier_payment_to_invoice(co):
    from app import hop_money
    from app.hop_doc_preview import create_manual_party_document as create
    with connect(co["path"]) as conn:
        cid = co["cust"]["id"]
        acct = conn.execute("SELECT id FROM hop_bank_accounts WHERE workspace_id=? LIMIT 1", (WS,)).fetchone()[0]
        pay = create(conn, WS, {"txn_type": 3, "customer_id": cid, "amount": 500, "received_amount": 500,
                                "payment_mode": "bank", "payment_account_id": acct})
        unused = hop_money.unused_payments(conn, WS, 1, "customer", cid)
        assert any(u["id"] == pay["party_txn_id"] and u["unused_amount"] == 500 for u in unused)
        inv = create(conn, WS, {"txn_type": 1, "customer_id": cid, "lines": [{"item_name": "Rod", "qty": 1, "rate": 1000}],
                                "link_payments": [{"payment_txn_id": pay["party_txn_id"], "amount": 300}]})
        bal = conn.execute("SELECT balance_amount FROM hop_party_transactions WHERE id=?", (inv["party_txn_id"],)).fetchone()[0]
        assert round(bal, 2) == 700
        left = {u["id"]: u["unused_amount"] for u in hop_money.unused_payments(conn, WS, 1, "customer", cid)}
        assert left[pay["party_txn_id"]] == 200

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


def test_note_mark_done_and_reopen(tmp_path):
    import sqlite3
    from app import hop_notes

    conn = sqlite3.connect(tmp_path / "n.db")
    conn.row_factory = sqlite3.Row
    a = hop_notes.create(conn, "ws", {"text": "first"})
    b = hop_notes.create(conn, "ws", {"text": "second"})
    assert a["done"] is False
    d = hop_notes.update(conn, "ws", a["id"], {"done": True, "done_by": "Claude"})
    assert d["done"] is True and d["done_by"] == "Claude"
    assert [n["id"] for n in hop_notes.search(conn, "ws")] == [b["id"], a["id"]]   # done ones go last
    d = hop_notes.update(conn, "ws", a["id"], {"done": False})
    assert d["done"] is False and d["done_by"] is None


def test_link_later_payment_to_saved_bill(tmp_path):
    """Bill saved first, payment made later without a bill → linked to the bill afterwards."""
    from app import hop_money
    from app.hop_db import connect, create_customer
    from app.hop_doc_preview import create_manual_party_document as create
    from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema

    path = str(tmp_path / "later.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        cid = create_customer(conn, WS, {"company": "Later Buyer"})["id"]
        inv = create(conn, WS, {"txn_type": 1, "customer_id": cid, "txn_date": "2026-11-05",
                                "lines": [{"item_name": "Cloth", "qty": 1, "rate": 1000, "tax_pct": 0}]})
        pay = create(conn, WS, {"txn_type": 3, "customer_id": cid, "txn_date": "2026-11-10", "amount": 400,
                                "payment_mode": "cash", "allocations": []})
        unused = {u["id"]: u["unused_amount"] for u in hop_money.unused_payments(conn, WS, 1, "customer", cid)}
        if pay["party_txn_id"] not in unused:
            pytest.fail("payment had no unused money")
        hop_money.link_payments_to_bill(conn, WS, inv["party_txn_id"], [{"payment_txn_id": pay["party_txn_id"], "amount": 400}])
        bal = conn.execute("SELECT balance_amount FROM hop_party_transactions WHERE id=?", (inv["party_txn_id"],)).fetchone()[0]
        assert round(bal, 2) == 600


def test_note_photos_add_and_remove(tmp_path):
    import io
    import os
    import sqlite3

    from PIL import Image

    from app import hop_notes

    db = str(tmp_path / "p.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    n = hop_notes.create(conn, "ws", {"text": "see screenshot"})
    buf = io.BytesIO()
    Image.new("RGB", (2400, 1200), "white").save(buf, "PNG")
    n = hop_notes.add_photo(conn, db, "ws", n["id"], buf.getvalue())
    assert len(n["photos"]) == 1
    pid = n["photos"][0]["id"]
    path = hop_notes.photo_path(db, "ws", n["id"], pid)
    assert os.path.exists(path) and max(Image.open(path).size) == 1600
    with pytest.raises(ValueError):
        hop_notes.add_photo(conn, db, "ws", n["id"], b"not an image")
    n = hop_notes.remove_photo(conn, db, "ws", n["id"], pid)
    assert n["photos"] == [] and not os.path.exists(path)

def test_note_raise_again_after_done(tmp_path):
    """Owner 21-11: Claude marks done, the user is not happy → raises it again; done shows again only when finished."""
    import sqlite3
    from app import hop_notes

    conn = sqlite3.connect(tmp_path / "n.db")
    conn.row_factory = sqlite3.Row
    n = hop_notes.create(conn, "ws", {"text": "[For Claude] fix the logo"})
    assert n["for_whom"] == "Claude" and n["raised_again"] == 0
    d = hop_notes.update(conn, "ws", n["id"], {"done": True, "done_by": "Claude"})
    assert d["done"] and d["comments"][-1]["text"] == "✓ Marked done" and d["comments"][-1]["by"] == "Claude"
    # raise again with a reason
    d = hop_notes.update(conn, "ws", n["id"], {"done": False, "done_by": "User", "reason": "logo still one letter"})
    assert not d["done"] and d["raised_again"] == 1
    assert d["comments"][-1]["text"] == "↩ Raised again: logo still one letter"
    # done again, by the user this time
    d = hop_notes.update(conn, "ws", n["id"], {"done": True, "done_by": "User"})
    assert d["done"] and d["done_by"] == "User"
    # a user's reply in a done note opens it again; Claude's reply does not
    d = hop_notes.add_comment(conn, "ws", n["id"], {"text": "ok thanks", "by": "Claude"})
    assert d["done"]
    d = hop_notes.add_comment(conn, "ws", n["id"], {"text": "still wrong on the bill", "by": "User"})
    assert not d["done"] and d["raised_again"] == 2 and d["comments"][-1]["kind"] == "status"
    # a note for nobody in particular
    assert hop_notes.create(conn, "ws", {"text": "call Sharma"})["for_whom"] is None


def test_note_done_with_link_and_for_claude_check(tmp_path):
    """Owner 21-11: done carries a link to the screen to check; only the founder writes notes for Claude."""
    import sqlite3

    import pytest
    from app import hop_notes

    conn = sqlite3.connect(tmp_path / "n.db")
    conn.row_factory = sqlite3.Row
    n = hop_notes.create(conn, "ws", {"text": "[For Claude] add company button"})
    d = hop_notes.update(conn, "ws", n["id"], {"done": True, "done_by": "Claude",
                                                "link": {"view": "dist_brand_desk", "label": "Distributor → Brand desk"}})
    assert d["comments"][-1]["link"] == {"view": "dist_brand_desk", "label": "Distributor → Brand desk"}
    with pytest.raises(ValueError):
        hop_notes.update(conn, "ws", n["id"], {"done": False})  # raising again is fine …
        hop_notes.add_comment(conn, "ws", n["id"], {"text": "x", "link": "../etc"})   # … a bad link is not
    assert hop_notes.is_for_claude({"party_name": "Claude"}) and hop_notes.is_for_claude({"text": "[For Claude] x"})
    assert not hop_notes.is_for_claude({"text": "call Sharma"})


def test_note_numbers_per_firm(tmp_path):
    """Owner 21-11: every note has a number in its firm; numbers never move; search "#2" finds note 2."""
    import sqlite3
    from app import hop_notes

    conn = sqlite3.connect(tmp_path / "n.db")
    conn.row_factory = sqlite3.Row
    a = hop_notes.create(conn, "ws", {"text": "first"})
    other = hop_notes.create(conn, "ws2", {"text": "other firm"})
    b = hop_notes.create(conn, "ws", {"text": "second"})
    c = hop_notes.create(conn, "ws", {"text": "third"})
    assert (a["no"], b["no"], c["no"], other["no"]) == (1, 2, 3, 1)
    hop_notes.delete(conn, "ws", b["id"])
    assert hop_notes.get(conn, "ws", c["id"])["no"] == 3
    assert [n["no"] for n in hop_notes.search(conn, "ws", "#3")] == [3]
    assert [n["no"] for n in hop_notes.search(conn, "ws", "3")] == [3]


def test_founder_double_tick_then_delete(tmp_path):
    """Owner 21-11: a done note for Claude gets the founder's ✓✓; only then it can be deleted; raise again clears it."""
    import sqlite3

    import pytest
    from app import hop_notes

    conn = sqlite3.connect(tmp_path / "n.db")
    conn.row_factory = sqlite3.Row
    n = hop_notes.create(conn, "ws", {"text": "[For Claude] fix the totals"})
    plain = hop_notes.create(conn, "ws", {"text": "call Sharma"})
    with pytest.raises(ValueError):
        hop_notes.update(conn, "ws", n["id"], {"checked": True})          # not done yet
    with pytest.raises(ValueError):
        hop_notes.update(conn, "ws", plain["id"], {"done": True, "checked": True})   # not a note for Claude
    with pytest.raises(ValueError):
        hop_notes.delete(conn, "ws", n["id"])                             # no ✓✓ yet
    hop_notes.update(conn, "ws", n["id"], {"done": True, "done_by": "Claude"})
    d = hop_notes.update(conn, "ws", n["id"], {"checked": True, "checked_by": "Founder"})
    assert d["checked"] and d["comments"][-1]["text"].startswith("✓✓")
    d = hop_notes.update(conn, "ws", n["id"], {"done": False, "reason": "one more fix"})
    assert not d["checked"] and not d["done"]                             # raise again clears ✓✓
    hop_notes.update(conn, "ws", n["id"], {"done": True, "done_by": "Claude"})
    hop_notes.update(conn, "ws", n["id"], {"checked": True})
    out = hop_notes.bulk_delete(conn, "ws", [n["id"], plain["id"]])
    assert out["deleted"] == 2 and out["skipped_not_checked"] == 0

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

"""Voice bill draft (QnA step 3) — AI split is passed in / mocked, so no AI cost."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_voice_bill as V
from app.hop_db import connect, create_customer
from app.hop_ops import create_product
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def db(tmp_path: Path):
    p = str(tmp_path / "v.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        create_customer(conn, WS, {"company": "Sharma Traders"})
        create_customer(conn, WS, {"company": "Gupta Stores"})
        create_product(conn, WS, {"name": "Aster Double Bedsheet", "selling_price": 750, "gst_pct": 5})
        create_product(conn, WS, {"name": "Aster King Bedsheet", "selling_price": 950, "gst_pct": 5})
        create_product(conn, WS, {"name": "Flora Towel", "code": "FL-2", "selling_price": 200, "gst_pct": 5})
        conn.commit()
    return p


def test_score_prefers_full_word_match():
    assert V.score("aster double", "Aster Double Bedsheet") > V.score("aster double", "Aster King Bedsheet")
    assert V.score("flora towel", "Flora Towel") >= 0.95
    assert V.score("sharma", "Sharma Traders") >= 0.85


def test_draft_matches_saved_lists_only(db):
    parsed = {"party": "Sharma", "lines": [
        {"item": "Aster double bedsheet", "qty": 10, "rate": 719},
        {"item": "Flora towel", "qty": 5, "rate": 0},
        {"item": "Aster", "qty": 2, "rate": 0},
        {"item": "Rose curtain", "qty": 1, "rate": 0}]}
    with connect(db) as conn:
        d = V.draft(conn, WS, "Sharma Traders, 10 Aster double ...", user_id=1, parsed=parsed)
    assert d["customer_match"] == "Sharma Traders" and d["customer_id"]
    a, f, amb, new = d["lines"]
    assert a["item_name"] == "Aster Double Bedsheet" and a["rate"] == 719 and a["rate_from"] == "spoken" and not a["unsure"]
    assert f["rate"] == 200 and f["rate_from"] == "catalogue" and f["matched_item"]
    assert amb["unsure"] and {o["item_name"] for o in amb["options"]} == {"Aster Double Bedsheet", "Aster King Bedsheet"}
    assert new["product_id"] is None and new["item_name"] == "Rose curtain" and new["rate_from"] == "missing"
    assert any("Rose curtain" in c for c in d["checks"]) and any("which one" in c for c in d["checks"])
    assert d["say"].startswith("Sharma Traders, 4 items, total ₹") and d["say"].endswith("Correct?")


def test_draft_party_not_found_and_empty_text(db):
    with connect(db) as conn:
        d = V.draft(conn, WS, "x", user_id=1, parsed={"party": "Mehta", "lines": []})
        assert d["customer_id"] is None and "not in your parties" in d["checks"][0]
        with pytest.raises(ValueError):
            V.draft(conn, WS, "  ", user_id=1, parsed={})


def test_voice_route(tmp_path, monkeypatch):
    from app.services import hop_ai
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    monkeypatch.setattr(hop_ai, "ask_json", lambda *a, **k: {"party": "", "lines": [{"item": "Lamp V", "qty": 3}]})
    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    client.post("/api/v1/hop/products", headers=headers, json={"name": "Lamp V", "selling_price": 100})
    r = client.post("/api/v1/hop/party-transactions/voice-draft", headers=headers, json={"text": "teen lamp v", "kind": "sale"})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    assert d["lines"][0]["item_name"] == "Lamp V" and d["lines"][0]["qty"] == 3
    assert client.post("/api/v1/hop/party-transactions/voice-draft", headers=headers,
                       json={"text": "x", "kind": "bad"}).status_code == 400

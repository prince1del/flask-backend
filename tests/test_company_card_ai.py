"""✨ Photo → company details, My brands registration summary, Ask Nexora brand / IFSC answers (no AI, no network)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_company_card_ai as CC
from app import hop_dist_brand as BR
from app import hop_distributor as D
from app import hop_dist_terms as T
from app.hop_db import connect
from app.hop_ops import create_vendor
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema
from app.services import hop_ai, ifsc_lookup


def _card(**kw):
    base = {"company": "Alpha Mills Pvt Ltd", "gst_no": "", "pan": "", "address": "", "city": "", "state": "",
            "pincode": "", "contact_person": "", "contact_designation": "", "mobile": "", "email": "",
            "bank_name": "", "bank_account": "", "bank_ifsc": "", "bank_holder": "", "doc_type": "visiting_card",
            "confidence": "high"}
    return {**base, **kw}


def test_photo_fills_state_pan_and_bank(monkeypatch):
    monkeypatch.setattr(hop_ai, "read_company_card", lambda d, m, user_id: _card(gst_no="27AABCA1234F1Z5", bank_ifsc="TSTB0000001"))
    monkeypatch.setattr(ifsc_lookup, "_fetch", lambda i: {"BANK": "Test Bank", "BRANCH": "MAIN", "ADDRESS": "1 Road",
                                                          "CITY": "Pune", "STATE": "Maharashtra"})
    import app.services.gstin_lookup as G
    monkeypatch.setattr(G, "_lookup_cleartax", lambda g: None, raising=False)
    monkeypatch.setattr(G, "_lookup_gstincheck", lambda g, k: None, raising=False)
    ifsc_lookup._CACHE.clear()
    out = CC.read(b"x", "image/jpeg", user_id=1)
    f = out["fields"]
    assert f["state"] == "Maharashtra" and f["pan"] == "AABCA1234F"
    assert f["bank_name"] == "Test Bank" and f["bank_branch"].startswith("MAIN")
    assert out["ai_used"] and not any("GSTIN not on" in c for c in out["checks"])


def test_photo_without_gstin_says_so(monkeypatch):
    monkeypatch.setattr(hop_ai, "read_company_card", lambda d, m, user_id: _card(company="", confidence="low"))
    out = CC.read(b"x", "image/jpeg", user_id=1)
    assert any("name not read" in c for c in out["checks"])
    assert any("GSTIN not on" in c for c in out["checks"])
    assert any("hard to read" in c for c in out["checks"])


@pytest.fixture
def firm(tmp_path: Path):
    path = str(tmp_path / "mb.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        BR.ensure_schema(conn)
        a = create_vendor(conn, WS, {"company": "Alpha Mills", "gst_no": "27AABCA1234F1Z5", "address": "1 Mill Road",
                                     "contact_person": "Ravi", "mobile": "9800000001"})
        b = create_vendor(conn, WS, {"company": "Beta Motors"})
        for v in (a, b):
            D.set_principal(conn, WS, v["id"], True)
        T.save_terms(conn, WS, a["id"], {"payment_terms": "30 days", "price_rule": "markup", "rule_pct": 20})
    return path


def test_registration_and_ask_nexora(firm):
    with connect(firm) as conn:
        reg = BR.registration(conn, WS)
        by = {b["brand"]: b for b in reg["brands"]}
        assert reg["count"] == 2 and reg["fully_registered"] == 1
        assert by["Alpha Mills"]["contact_person"] == "Ravi" and not by["Alpha Mills"]["missing"]
        assert len(by["Beta Motors"]["missing"]) == 3
        one = hop_ai.books_answer(conn, WS, "dist_my_brands", party_name="alpha mills")
        assert [b["brand"] for b in one["brands"]] == ["Alpha Mills"]
        assert hop_ai.books_answer(conn, WS, "ifsc", party_name="bad")["ok"] is False

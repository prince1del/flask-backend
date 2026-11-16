"""HoP sharing: UPI link + WhatsApp message template (made-up company)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_app_settings as S, hop_share
from app.hop_db import connect, create_customer
from app.hop_doc_preview import build_txn_preview, create_manual_party_document as create
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


def test_upi_and_whatsapp(tmp_path: Path):
    p = str(tmp_path / "share.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        cust = create_customer(conn, WS, {"company": "Hotel W", "mobile": "98765 43210"})
        inv = create(conn, WS, {"txn_type": 1, "customer_id": cust["id"], "txn_date": "2026-11-16",
                                "lines": [{"item_name": "x", "qty": 1, "rate": 1000}]})
        # No UPI ID yet → no link
        assert build_txn_preview(conn, WS, party_txn_id=inv["party_txn_id"]).get("upi") is None
        with pytest.raises(ValueError):
            S.save(conn, WS, {"upi_id": "not an id"})
        S.save(conn, WS, {"upi_id": "testshop@okbank", "upi_name": "Test Shop"})
        pv = build_txn_preview(conn, WS, party_txn_id=inv["party_txn_id"])
        assert pv["upi"]["link"].startswith("upi://pay?pa=testshop%40okbank") and "am=1000.00" in pv["upi"]["link"]
        import base64
        assert base64.b64decode(pv["upi"]["qr_png"])[1:4] == b"PNG"
        sh = hop_share.share_payload(conn, WS, inv["party_txn_id"])
        assert sh["phone"] == "919876543210" and sh["whatsapp_url"].startswith("https://wa.me/919876543210?text=")
        assert "Hotel W" in sh["text"] and "upi://pay" in sh["text"]
        S.save(conn, WS, {"msg_bill": "Hi {party}, bill {number} ₹{amount}"})
        assert hop_share.share_payload(conn, WS, inv["party_txn_id"])["text"].startswith("Hi Hotel W, bill ")
        rem = hop_share.share_payload(conn, WS, inv["party_txn_id"], "reminder")["text"]
        assert "gentle reminder" in rem and "₹1,000" in rem

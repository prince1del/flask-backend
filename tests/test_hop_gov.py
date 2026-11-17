"""Batch 5 — Government & approvals: locked keys, owner only, e-invoice IRP (fake NIC server doing the real
encryption), Razorpay payment link + signed webhook → Payment-In, SMS provider. No real network."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path

import pytest

from app import hop_gov, hop_gov_einvoice as EI, hop_gov_payments as PAY, hop_gov_sms as SMS
from app.hop_db import connect, create_customer
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def db(tmp_path: Path):
    p = str(tmp_path / "gov.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        conn.execute("INSERT INTO hop_firm_profile (workspace_id, firm_name, gstin, state, address, updated_at) "
                     "VALUES (?, 'Seller Co', '07AAACS1234A1Z5', 'Delhi', 'Shop 1, Delhi 110001', '2026') "
                     "ON CONFLICT(workspace_id) DO UPDATE SET gstin='07AAACS1234A1Z5', state='Delhi', address='Shop 1, Delhi 110001'", (WS,))
        c = create_customer(conn, WS, {"company": "Buyer Hotel", "mobile": "9876543210", "gst_no": "06AAACB1234A1Z5",
                                       "state": "Haryana", "address": "Plot 2, Gurugram 122001"})
        t = create(conn, WS, {"txn_type": 1, "customer_id": c["id"], "txn_date": "2026-11-17",
                              "lines": [{"item_name": "Curtain", "hsn": "6303", "qty": 2, "rate": 1000, "tax_pct": 12, "unit": "PCS"}]})
        conn.commit()
    return p, int(t["party_txn_id"]), c


def test_keys_locked_and_hidden(db):
    p, _, _ = db
    with connect(p) as conn:
        with pytest.raises(ValueError, match="Fill these first"):
            hop_gov.save(conn, WS, "payments", "test", {"key_id": "rzp_test_x"})
        hop_gov.save(conn, WS, "payments", "test", {"key_id": "rzp_test_abc", "key_secret": "topsecret99", "webhook_secret": "wh"})
        raw = conn.execute("SELECT data FROM hop_gov_config WHERE service='payments'").fetchone()[0]
        assert "topsecret99" not in raw                                    # stored locked
        st = {s["service"]: s for s in hop_gov.status(conn, WS, "https://x/")}
        f = {x["key"]: x for x in st["payments"]["fields"]}
        assert f["key_secret"]["value"] == "" and f["key_secret"]["hint"].endswith("et99")   # never sent back
        assert st["payments"]["webhook_url"].endswith("/razorpay-webhook/" + WS)
        hop_gov.save(conn, WS, "payments", None, {"key_secret": ""})                   # blank keeps the saved one
        assert hop_gov.config(conn, WS, "payments")["key_secret"] == "topsecret99"


def test_einvoice_irn_with_fake_nic(db, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    p, tid, _ = db
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    sek = os.urandom(32)
    seen = {}

    def fake(method, url, headers=None, body=None, timeout=30):
        if url.endswith("/eivital/v1.04/auth"):
            login = json.loads(priv.decrypt(base64.b64decode(body["Data"]), padding.PKCS1v15()))
            assert login["UserName"] == "apiuser" and headers["client_id"] == "cid"
            app_key = base64.b64decode(login["AppKey"])
            return 200, {"Status": 1, "Data": {"AuthToken": "TOK", "Sek": base64.b64encode(EI._aes(app_key, EI._pad(sek), True)).decode()}}
        assert headers["AuthToken"] == "TOK"
        if url.endswith("/eicore/v1.03/Invoice"):
            inv = EI.dec_json(sek, body["Data"])
            seen["inv"] = inv
            return 200, {"Status": 1, "Data": EI.enc_json(sek, {"Irn": "a" * 64, "AckNo": 112210000001, "AckDt": "2026-11-17 10:00:00",
                                                                 "SignedQRCode": "eyJ.signed.qr"})}
        if url.endswith("/Invoice/Cancel"):
            return 200, {"Status": 1, "Data": EI.enc_json(sek, {"Irn": "a" * 64, "CancelDate": "2026-11-17 11:00:00"})}
        if "/Master/gstin/" in url:
            return 200, {"Status": 1, "Data": EI.enc_json(sek, {"Gstin": "06AAACB1234A1Z5", "LegalName": "BUYER HOTEL PVT LTD", "Status": "ACT"})}
        return 404, {}

    monkeypatch.setattr(hop_gov, "http_json", fake)
    EI._tokens.clear()
    with connect(p) as conn:
        with pytest.raises(ValueError, match="off"):
            EI.generate_irn(conn, WS, tid)
        hop_gov.save(conn, WS, "einvoice", "test", {"client_id": "cid", "client_secret": "cs", "username": "apiuser",
                                                    "password": "pw", "gstin": "07AAACS1234A1Z5", "public_key": pem})
        assert EI.test_keys(conn, WS)[0]
        r = EI.generate_irn(conn, WS, tid)
        assert r["irn"] == "a" * 64 and r["qr"]
        assert seen["inv"]["SellerDtls"]["Gstin"] == "07AAACS1234A1Z5" and seen["inv"]["ItemList"][0]["HsnCd"] == "6303"
        row = conn.execute("SELECT irn, ack_no, einv_qr FROM hop_party_transactions WHERE id=?", (tid,)).fetchone()
        assert row[0] == "a" * 64 and row[1] == "112210000001" and row[2] == "eyJ.signed.qr"
        assert EI.gstin_details(conn, WS, "06AAACB1234A1Z5")["legal_name"] == "BUYER HOTEL PVT LTD"
        assert EI.cancel_irn(conn, WS, tid, "2", "typo")["cancelled"]
        assert conn.execute("SELECT irn FROM hop_party_transactions WHERE id=?", (tid,)).fetchone()[0] is None


def test_payment_link_and_webhook(db, monkeypatch):
    p, tid, c = db
    calls = []

    def fake(method, url, headers=None, body=None, timeout=30):
        calls.append((method, url, body))
        assert headers["Authorization"].startswith("Basic ")
        return 200, {"id": "plink_1", "short_url": "https://rzp.io/i/abc"}

    monkeypatch.setattr(hop_gov, "http_json", fake)
    with connect(p) as conn:
        hop_gov.save(conn, WS, "payments", "test", {"key_id": "rzp_test_abc", "key_secret": "s", "webhook_secret": "whsec"})
        due = conn.execute("SELECT balance_amount FROM hop_party_transactions WHERE id=?", (tid,)).fetchone()[0]
        link = PAY.create_link(conn, WS, tid)
        assert link["url"] == "https://rzp.io/i/abc" and calls[0][2]["amount"] == int(round(due * 100))
        assert PAY.create_link(conn, WS, tid)["reused"] and len(calls) == 1
        ev = json.dumps({"event": "payment_link.paid", "payload": {
            "payment_link": {"entity": {"id": "plink_1", "amount_paid": int(round(due * 100))}},
            "payment": {"entity": {"id": "pay_9", "amount": int(round(due * 100))}}}}).encode()
        with pytest.raises(PermissionError):
            PAY.webhook(conn, WS, ev, "wrong")
        sig = hmac.new(b"whsec", ev, hashlib.sha256).hexdigest()
        r = PAY.webhook(conn, WS, ev, sig)
        assert r["saved"] and abs(r["amount"] - due) < 0.01
        assert PAY.webhook(conn, WS, ev, sig) == {"already": True}                 # never twice
        bal = conn.execute("SELECT balance_amount FROM hop_party_transactions WHERE id=?", (tid,)).fetchone()[0]
        assert abs(bal) < 0.01
        acct = conn.execute("SELECT current_balance FROM hop_bank_accounts WHERE workspace_id=? AND name='Razorpay'", (WS,)).fetchone()[0]
        assert abs(acct - due) < 0.01


def test_sms_provider(db, monkeypatch):
    p, _, _ = db
    sent = []
    monkeypatch.setattr(hop_gov, "http_json", lambda m, u, h=None, b=None, timeout=30: (sent.append((u, h, b)), (200, {"type": "success"}))[1])
    with connect(p) as conn:
        with pytest.raises(ValueError, match="off"):
            SMS.send(conn, WS, "9876543210", "hi")
        hop_gov.save(conn, WS, "sms", "test", {"provider": "msg91", "auth_key": "k", "sender_id": "NEXORA", "template_bill": "T1"})
        assert SMS.send(conn, WS, "98765 43210", "Your bill", "bill")["to"] == "919876543210"
        assert sent[0][1]["authkey"] == "k" and sent[0][2]["template_id"] == "T1"
        with pytest.raises(ValueError):
            SMS.send(conn, WS, "123", "x")


def test_routes_owner_only(tmp_path, monkeypatch):
    from tests.test_hop_permissions import _staff
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, owner, db_path = _setup_hop_wipe_client(tmp_path, monkeypatch)
    staff, _ = _staff(client, db_path)
    assert client.get("/api/v1/hop/gov", headers=owner).status_code == 200
    assert client.get("/api/v1/hop/gov", headers=staff).status_code == 403
    assert client.put("/api/v1/hop/gov/payments", headers=staff, json={"mode": "live"}).status_code == 403
    r = client.put("/api/v1/hop/gov/sms", headers=owner, json={"mode": "off", "values": {"sender_id": "NEXORA"}})
    assert r.status_code == 200
    bad = client.post("/api/v1/public/razorpay-webhook/" + "house_of_prizm", data=b"{}", headers={"X-Razorpay-Signature": "x"})
    assert bad.status_code == 400

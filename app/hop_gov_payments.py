"""Online payment link on a sale invoice — the business's OWN Razorpay account (Settings → Government & approvals).

1. "Pay online" on a bill → Razorpay payment link for the money still due (customer's name / phone filled).
2. Customer pays → Razorpay calls our webhook (signed with the business's webhook secret) → a Payment-In is
   saved by itself into the "Razorpay" bank account and linked to the bill, so the bill shows Paid.
   Paying twice / the webhook coming twice never saves the money twice.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

API = "https://api.razorpay.com/v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS hop_pay_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, party_txn_id INTEGER NOT NULL,
        link_id TEXT UNIQUE, url TEXT, amount REAL, status TEXT, payment_id TEXT, payment_txn_id INTEGER,
        created_at TEXT, paid_at TEXT)""")


def _cfg(conn, ws) -> dict[str, Any]:
    from app import hop_gov

    cfg = hop_gov.live(conn, ws, "payments")
    if not cfg:
        raise ValueError("Online payments are off — switch them on in Settings → Government & approvals")
    return cfg


def _auth(cfg: dict) -> dict[str, str]:
    tok = base64.b64encode(f"{cfg['key_id']}:{cfg['key_secret']}".encode()).decode()
    return {"Authorization": f"Basic {tok}"}


def create_link(conn: sqlite3.Connection, ws: str, txn_id: int) -> dict[str, Any]:
    from app import hop_gov

    ensure_schema(conn)
    cfg = _cfg(conn, ws)
    conn.row_factory = sqlite3.Row
    t = conn.execute("SELECT * FROM hop_party_transactions WHERE workspace_id=? AND id=? AND txn_type IN (1, 83)",
                     (ws, int(txn_id))).fetchone()
    if not t:
        raise ValueError("Sale invoice not found")
    due = round(float(t["balance_amount"] or 0), 2)
    if due <= 0.009:
        raise ValueError("Nothing is due on this bill")
    old = conn.execute("SELECT url, amount FROM hop_pay_links WHERE workspace_id=? AND party_txn_id=? AND status='created' "
                       "ORDER BY id DESC LIMIT 1", (ws, int(txn_id))).fetchone()
    if old and abs(float(old["amount"]) - due) < 0.01:
        return {"url": old["url"], "amount": due, "reused": True}
    phone = ""
    if t["party_id"]:
        c = conn.execute("SELECT mobile FROM hop_customers WHERE workspace_id=? AND id=?", (ws, t["party_id"])).fetchone()
        phone = "".join(ch for ch in (c["mobile"] if c else "") or "" if ch.isdigit())[-10:]
    body = {"amount": int(round(due * 100)), "currency": "INR", "accept_partial": False,
            "description": f"{t['txn_number'] or 'Invoice'}"[:250],
            "customer": {"name": (t["party_name"] or "Customer")[:50], **({"contact": "+91" + phone} if len(phone) == 10 else {})},
            "notify": {"sms": False, "email": False}, "reminder_enable": False,
            "reference_id": f"nx{int(txn_id)}-{int(time.time())}"[:40], "notes": {"workspace": ws, "txn_id": str(int(txn_id))}}
    code, res = hop_gov.http_json("POST", f"{API}/payment_links", _auth(cfg), body)
    if code not in (200, 201) or not res.get("short_url"):
        msg = (res.get("error") or {}).get("description") if isinstance(res.get("error"), dict) else res.get("error")
        raise ValueError(f"Razorpay did not make the link: {msg or code}")
    conn.execute("INSERT INTO hop_pay_links (workspace_id, party_txn_id, link_id, url, amount, status, created_at) "
                 "VALUES (?,?,?,?,?,'created',?)", (ws, int(txn_id), res["id"], res["short_url"], due, _now()))
    conn.commit()
    return {"url": res["short_url"], "amount": due, "reused": False}


def _razorpay_account(conn, ws) -> int:
    r = conn.execute("SELECT id FROM hop_bank_accounts WHERE workspace_id=? AND name='Razorpay' AND account_type!='cash'",
                     (ws,)).fetchone()
    if r:
        return int(r[0])
    now = _now()
    return int(conn.execute("INSERT INTO hop_bank_accounts (workspace_id, name, account_type, opening_balance, current_balance, "
                            "is_active, created_at, updated_at) VALUES (?, 'Razorpay', 'bank', 0, 0, 1, ?, ?)",
                            (ws, now, now)).lastrowid)


def webhook(conn: sqlite3.Connection, ws: str, raw: bytes, signature: str) -> dict[str, Any]:
    """Razorpay → us. Checked with the business's webhook secret; only payment_link.paid saves money."""
    from app.hop_doc_preview import create_manual_party_document

    ensure_schema(conn)
    cfg = _cfg(conn, ws)
    secret = str(cfg.get("webhook_secret") or "")
    good = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(good, signature or ""):
        raise PermissionError("Bad signature")
    ev = json.loads(raw.decode() or "{}")
    if ev.get("event") != "payment_link.paid":
        return {"ignored": ev.get("event")}
    pl = ((ev.get("payload") or {}).get("payment_link") or {}).get("entity") or {}
    pay = ((ev.get("payload") or {}).get("payment") or {}).get("entity") or {}
    conn.row_factory = sqlite3.Row
    link = conn.execute("SELECT * FROM hop_pay_links WHERE workspace_id=? AND link_id=?", (ws, pl.get("id"))).fetchone()
    if not link:
        return {"ignored": "unknown link"}
    if link["status"] == "paid":
        return {"already": True}
    t = conn.execute("SELECT * FROM hop_party_transactions WHERE workspace_id=? AND id=?", (ws, link["party_txn_id"])).fetchone()
    amount = round(float(pay.get("amount") or pl.get("amount_paid") or 0) / 100.0, 2)
    due = round(float(t["balance_amount"] or 0), 2) if t else 0
    acct = _razorpay_account(conn, ws)
    doc = create_manual_party_document(conn, ws, {
        "txn_type": 3, "customer_id": t["party_id"], "txn_date": datetime.now().date().isoformat(), "amount": amount,
        "payment_account_id": acct, "allocations": [{"txn_id": t["id"], "amount": min(amount, due)}] if due > 0 else [],
        "notes": f"Paid online (Razorpay {pay.get('id') or ''}) for {t['txn_number'] or ''}"})
    conn.execute("UPDATE hop_pay_links SET status='paid', payment_id=?, payment_txn_id=?, paid_at=? WHERE id=?",
                 (pay.get("id"), doc.get("party_txn_id"), _now(), link["id"]))
    conn.commit()
    return {"saved": True, "payment_txn_id": doc.get("party_txn_id"), "amount": amount}


def test_keys(conn: sqlite3.Connection, ws: str) -> tuple[bool, str]:
    from app import hop_gov

    cfg = hop_gov.config(conn, ws, "payments")
    if not cfg.get("key_id") or not cfg.get("key_secret"):
        return False, "Fill Key ID and Key Secret first"
    code, res = hop_gov.http_json("GET", f"{API}/payment_links?count=1", _auth(cfg))
    if code == 200:
        return True, ("Test keys work" if str(cfg["key_id"]).startswith("rzp_test") else "Live keys work")
    return False, f"Razorpay said no ({code}) — check the keys"

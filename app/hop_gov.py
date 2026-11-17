"""Government & approval services for a business (Batch 5): built now, switched on after approval.

Each service has a mode: ``off`` (default), ``test`` (provider's sandbox) or ``live``. The owner enters the keys
in Settings → Government & approvals; keys are stored LOCKED (AES-256-GCM with this company's key — the same
key family as backups) and are never sent back to the screen, only "saved ✓ …last 4".

Services
* ``einvoice``  — NIC IRP API (or a GSP using the same API): IRN, signed QR, cancel, E-Way Bill by IRN, GSTIN details.
* ``ewaybill`` — uses the e-invoice login (E-Way Bill by IRN); standalone EWB API keys can be added later.
* ``sms``      — DLT SMS through a provider (MSG91 flow API, or any URL template).
* ``payments`` — the business's OWN Razorpay account: payment link on a bill; paid → Payment-In by itself.
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

SERVICES: dict[str, dict[str, Any]] = {
    "einvoice": {
        "title": "E-Invoice (IRN) + E-Way Bill + GSTIN verify",
        "apply": "Register on the e-invoice portal (einvoice1.gst.gov.in) → API registration → choose 'Through GSP' "
                 "(or 'Direct' if eligible). You get a Client ID, Client Secret and create an API user name + password.",
        "where": "https://einvoice1.gst.gov.in",
        "fields": [("client_id", "Client ID", False), ("client_secret", "Client Secret", True),
                   ("username", "API user name", False), ("password", "API password", True),
                   ("gstin", "Your GSTIN (as registered)", False), ("public_key", "IRP public key (PEM text from the portal)", False),
                   ("base_url", "API address (leave blank for NIC)", False)],
        "secret_keys": {"client_secret", "password"},
    },
    "sms": {
        "title": "SMS (DLT)",
        "apply": "Register your business on a DLT portal (Jio / Airtel / Vi), get a Sender ID (6 letters) and approve "
                 "message templates; then open an account with an SMS provider (e.g. MSG91) and copy its auth key.",
        "where": "https://trueconnect.jio.com",
        "fields": [("provider", "Provider (msg91 or url)", False), ("auth_key", "Auth key", True),
                   ("sender_id", "Sender ID (6 letters)", False), ("template_bill", "DLT template ID — bill", False),
                   ("template_reminder", "DLT template ID — payment reminder", False),
                   ("url", "Send URL (only for provider 'url'; use {to} {text})", False)],
        "secret_keys": {"auth_key"},
    },
    "payments": {
        "title": "Online payment link (Razorpay)",
        "apply": "Open a Razorpay account in your business name and finish KYC. In Razorpay Dashboard → Settings → "
                 "API Keys make a key; in Webhooks add NEXORA's address (shown below) with event payment_link.paid.",
        "where": "https://dashboard.razorpay.com",
        "fields": [("key_id", "Key ID (rzp_test_… or rzp_live_…)", False), ("key_secret", "Key Secret", True),
                   ("webhook_secret", "Webhook secret", True)],
        "secret_keys": {"key_secret", "webhook_secret"},
    },
}
MODES = ("off", "test", "live")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS hop_gov_config (
        workspace_id TEXT NOT NULL, service TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'off',
        data TEXT, updated_at TEXT, last_test TEXT, PRIMARY KEY (workspace_id, service))""")


def _lock(ws: str, obj: dict) -> str:
    from app import hop_backup

    return base64.b64encode(hop_backup.lock(json.dumps(obj).encode(), ws)).decode()


def _unlock(ws: str, blob: str | None) -> dict:
    from app import hop_backup

    if not blob:
        return {}
    try:
        return json.loads(hop_backup.unlock(base64.b64decode(blob), ws))
    except Exception:
        return {}


def config(conn: sqlite3.Connection, ws: str, service: str) -> dict[str, Any]:
    """Full config with keys — for the server only, never for the screen."""
    ensure_schema(conn)
    r = conn.execute("SELECT mode, data FROM hop_gov_config WHERE workspace_id=? AND service=?", (ws, service)).fetchone()
    return {"mode": (r[0] if r else "off"), **(_unlock(ws, r[1]) if r else {})}


def status(conn: sqlite3.Connection, ws: str, host_url: str = "") -> list[dict[str, Any]]:
    """For the screen: mode, which fields are filled (secrets shown as ✓ …last 4), what to apply for."""
    ensure_schema(conn)
    out = []
    for key, sv in SERVICES.items():
        cfg = config(conn, ws, key)
        last = conn.execute("SELECT last_test FROM hop_gov_config WHERE workspace_id=? AND service=?", (ws, key)).fetchone()
        fields = []
        for f, label, secret in sv["fields"]:
            v = str(cfg.get(f) or "")
            fields.append({"key": f, "label": label, "secret": secret,
                           "value": ("" if secret else v), "saved": bool(v), "hint": (f"saved ✓ …{v[-4:]}" if secret and v else "")})
        item = {"service": key, "title": sv["title"], "apply": sv["apply"], "where": sv["where"], "mode": cfg["mode"],
                "fields": fields, "last_test": json.loads(last[0]) if last and last[0] else None}
        if key == "payments" and host_url:
            item["webhook_url"] = host_url.rstrip("/") + "/api/v1/public/razorpay-webhook/" + ws
        out.append(item)
    return out


def save(conn: sqlite3.Connection, ws: str, service: str, mode: str | None, values: dict[str, Any]) -> None:
    if service not in SERVICES:
        raise ValueError("Unknown service")
    if mode is not None and mode not in MODES:
        raise ValueError("Mode must be off, test or live")
    cur = config(conn, ws, service)
    new_mode = mode or cur.pop("mode", "off")
    cur.pop("mode", None)
    allowed = {f for f, _, _ in SERVICES[service]["fields"]}
    for k, v in (values or {}).items():
        if k in allowed and v is not None:
            v = str(v).strip()
            if v or k not in SERVICES[service]["secret_keys"]:   # blank secret = keep the saved one
                cur[k] = v[:4000] if k == "public_key" else v[:400]
    if new_mode != "off":
        need = [label for f, label, _ in SERVICES[service]["fields"] if f in _required(service, cur) and not cur.get(f)]
        if need:
            raise ValueError("Fill these first: " + ", ".join(need))
    conn.execute("INSERT INTO hop_gov_config (workspace_id, service, mode, data, updated_at) VALUES (?,?,?,?,?) "
                 "ON CONFLICT(workspace_id, service) DO UPDATE SET mode=excluded.mode, data=excluded.data, updated_at=excluded.updated_at",
                 (ws, service, new_mode, _lock(ws, cur), _now()))
    conn.commit()


def _required(service: str, cur: dict) -> set[str]:
    if service == "einvoice":
        return {"client_id", "client_secret", "username", "password", "gstin", "public_key"}
    if service == "sms":
        return {"auth_key", "sender_id"} if (cur.get("provider") or "msg91") == "msg91" else {"url"}
    if service == "payments":
        return {"key_id", "key_secret"}
    return set()


def note_test(conn: sqlite3.Connection, ws: str, service: str, ok: bool, message: str) -> dict[str, Any]:
    res = {"ok": ok, "message": message[:300], "at": _now()}
    conn.execute("UPDATE hop_gov_config SET last_test=? WHERE workspace_id=? AND service=?", (json.dumps(res), ws, service))
    conn.commit()
    return res


def live(conn: sqlite3.Connection, ws: str, service: str) -> dict[str, Any] | None:
    """Config when the service is switched on (test or live), else None."""
    cfg = config(conn, ws, service)
    return cfg if cfg.get("mode") in ("test", "live") else None


def http_json(method: str, url: str, headers: dict[str, str] | None = None, body: Any = None,
              timeout: int = 30) -> tuple[int, dict[str, Any]]:
    """Small HTTP helper (tests replace it)."""
    import urllib.error
    import urllib.request

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode() or "{}"
            return r.status, json.loads(txt)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except ValueError:
            return e.code, {"error": str(e)}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, {"error": str(e)}


_ = os  # keep import (env reads in providers)

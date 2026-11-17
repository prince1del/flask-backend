"""SMS sent by NEXORA itself through a DLT-registered provider (Settings → Government & approvals → SMS).

Until this is switched on, the phone's own SMS app is used (the user taps Send) — that keeps working always.
Providers: ``msg91`` (Flow API with a DLT template id) or ``url`` (any provider's GET/POST URL with {to} {text}).
In India the text must match the approved DLT template, so each kind (bill / reminder) has its own template id.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any
from urllib.parse import quote


def _digits(phone: str) -> str:
    d = re.sub(r"\D", "", str(phone or ""))
    if len(d) == 10:
        d = "91" + d
    return d if 11 <= len(d) <= 13 else ""


def send(conn: sqlite3.Connection, ws: str, phone: str, text: str, kind: str = "bill",
         variables: dict[str, str] | None = None) -> dict[str, Any]:
    from app import hop_gov

    cfg = hop_gov.live(conn, ws, "sms")
    if not cfg:
        raise ValueError("SMS sending is off — switch it on in Settings → Government & approvals")
    to = _digits(phone)
    if not to:
        raise ValueError("No valid mobile number")
    provider = (cfg.get("provider") or "msg91").lower()
    if provider == "url":
        url = str(cfg.get("url") or "").replace("{to}", to).replace("{text}", quote(text[:900]))
        code, res = hop_gov.http_json("GET", url)
    else:
        tpl = cfg.get(f"template_{kind}") or cfg.get("template_bill")
        if not tpl:
            raise ValueError(f"Add the DLT template ID for {kind} SMS first")
        body = {"template_id": tpl, "short_url": "0", "recipients": [{"mobiles": to, **(variables or {"text": text[:300]})}]}
        code, res = hop_gov.http_json("POST", "https://control.msg91.com/api/v5/flow/", {"authkey": cfg["auth_key"]}, body)
    if code not in (200, 201) or (isinstance(res, dict) and str(res.get("type") or "").lower() == "error"):
        raise ValueError(f"SMS not sent ({code}): {res.get('message') if isinstance(res, dict) else res}")
    return {"sent": True, "to": to, "mode": cfg["mode"]}


def test_keys(conn: sqlite3.Connection, ws: str, phone: str) -> tuple[bool, str]:
    try:
        send(conn, ws, phone, "NEXORA test message", "bill")
        return True, f"Test SMS sent to {phone}"
    except ValueError as exc:
        return False, str(exc)

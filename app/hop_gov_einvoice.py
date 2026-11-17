"""E-Invoice (IRN), E-Way Bill by IRN and GSTIN details straight from the IRP (NIC API v1.03/1.04).

Until switched on, the JSON-file route (app/hop_einvoice.py: download → upload on the portal → type the IRN back)
keeps working. When the owner adds the IRP API login in Settings → Government & approvals:

* Auth: the login + a fresh random AppKey are RSA-encrypted with the IRP public key → AuthToken + session key (SEK,
  AES-256 encrypted with the AppKey). Token kept ~6 hours per firm (in memory).
* Every request body is AES-256-ECB encrypted with the SEK; answers come back encrypted the same way.
* Generate IRN → saves IRN, Ack no / date and the SIGNED QR on the invoice (printed on the bill).
* Cancel IRN (within 24 hours on the portal's rules), E-Way Bill from the IRN, GSTIN details.

``base_url`` empty = NIC sandbox in test mode / NIC production in live mode; a GSP's NIC-compatible address can be
typed instead. The IRP public key (PEM, from the portal) goes in the ``public_key`` field.
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
from typing import Any

SANDBOX = "https://einv-apisandbox.nic.in"
LIVE = "https://einvoice1.gst.gov.in"     # production API host given after approval may differ — type it in base_url
_tokens: dict[str, dict[str, Any]] = {}


def _pad(b: bytes) -> bytes:
    n = 16 - len(b) % 16
    return b + bytes([n]) * n


def _unpad(b: bytes) -> bytes:
    return b[: -b[-1]] if b and 1 <= b[-1] <= 16 else b


def _aes(key: bytes, data: bytes, encrypt: bool) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    c = Cipher(algorithms.AES(key), modes.ECB())
    op = c.encryptor() if encrypt else c.decryptor()
    return op.update(data) + op.finalize()


def enc_json(sek: bytes, obj: Any) -> str:
    return base64.b64encode(_aes(sek, _pad(json.dumps(obj).encode()), True)).decode()


def dec_json(sek: bytes, b64: str) -> Any:
    raw = _unpad(_aes(sek, base64.b64decode(b64), False))
    try:
        return json.loads(raw)
    except ValueError:
        return json.loads(base64.b64decode(raw))


def _base(cfg: dict) -> str:
    return (cfg.get("base_url") or (SANDBOX if cfg.get("mode") == "test" else LIVE)).rstrip("/")


def _cfg(conn, ws) -> dict[str, Any]:
    from app import hop_gov

    cfg = hop_gov.live(conn, ws, "einvoice")
    if not cfg:
        raise ValueError("E-Invoice API is off — use the JSON file, or switch it on in Settings → Government & approvals")
    return cfg


def _rsa(public_pem: str, data: bytes) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    pem = public_pem.strip()
    if "BEGIN" not in pem:
        pem = "-----BEGIN PUBLIC KEY-----\n" + pem + "\n-----END PUBLIC KEY-----"
    key = serialization.load_pem_public_key(pem.encode())
    return base64.b64encode(key.encrypt(data, padding.PKCS1v15())).decode()


def _headers(cfg: dict, tok: dict | None = None) -> dict[str, str]:
    h = {"client_id": cfg["client_id"], "client_secret": cfg["client_secret"], "Gstin": cfg["gstin"]}
    if tok:
        h.update({"user_name": cfg["username"], "AuthToken": tok["token"]})
    return h


def _errors(res: dict) -> str:
    det = res.get("ErrorDetails") or res.get("error") or []
    if isinstance(det, list):
        return "; ".join(f"{d.get('ErrorCode', '')} {d.get('ErrorMessage', '')}".strip() for d in det if isinstance(d, dict)) or "rejected"
    return str(det or res.get("message") or "rejected")


def auth(cfg: dict, ws: str, force: bool = False) -> dict[str, Any]:
    from app import hop_gov

    key = f"{ws}|{cfg.get('mode')}"
    t = _tokens.get(key)
    if t and not force and t["until"] > time.time():
        return t
    if not cfg.get("public_key"):
        raise ValueError("Paste the IRP public key (from the portal) in Settings → Government & approvals")
    app_key = os.urandom(32)
    payload = {"UserName": cfg["username"], "Password": cfg["password"], "AppKey": base64.b64encode(app_key).decode(),
               "ForceRefreshAccessToken": bool(force)}
    code, res = hop_gov.http_json("POST", _base(cfg) + "/eivital/v1.04/auth", _headers(cfg),
                                  {"Data": _rsa(cfg["public_key"], json.dumps(payload).encode())})
    if code != 200 or str(res.get("Status")) != "1":
        raise ValueError("IRP login failed: " + _errors(res))
    d = res["Data"]
    sek = _unpad(_aes(app_key, base64.b64decode(d["Sek"]), False))
    t = {"token": d["AuthToken"], "sek": sek, "until": time.time() + 5.5 * 3600}
    _tokens[key] = t
    return t


def _call(cfg: dict, ws: str, method: str, path: str, body: Any = None) -> Any:
    from app import hop_gov

    for attempt in (0, 1):
        tok = auth(cfg, ws, force=bool(attempt))
        data = {"Data": enc_json(tok["sek"], body)} if body is not None else None
        code, res = hop_gov.http_json(method, _base(cfg) + path, _headers(cfg, tok), data)
        if code == 200 and str(res.get("Status")) == "1":
            return dec_json(tok["sek"], res["Data"]) if isinstance(res.get("Data"), str) else res.get("Data")
        msg = _errors(res)
        if attempt == 0 and ("1005" in msg or "token" in msg.lower()):   # token expired → log in again once
            continue
        raise ValueError(msg)
    raise ValueError("IRP did not answer")


def generate_irn(conn: sqlite3.Connection, ws: str, txn_id: int) -> dict[str, Any]:
    from app import hop_einvoice

    cfg = _cfg(conn, ws)
    j = hop_einvoice.einvoice_json(conn, ws, [int(txn_id)])
    if j["problems"]:
        raise ValueError("Fix first: " + "; ".join(j["problems"][:6]))
    d = _call(cfg, ws, "POST", "/eicore/v1.03/Invoice", j["json"][0])
    conn.execute("UPDATE hop_party_transactions SET irn=?, ack_no=?, ack_date=?, eway_bill_no=COALESCE(?, eway_bill_no), "
                 "updated_at=datetime('now') WHERE workspace_id=? AND id=?",
                 (d.get("Irn"), str(d.get("AckNo") or ""), str(d.get("AckDt") or ""), d.get("EwbNo"), ws, int(txn_id)))
    _save_qr(conn, ws, int(txn_id), d.get("SignedQRCode") or "")
    conn.commit()
    return {"irn": d.get("Irn"), "ack_no": d.get("AckNo"), "ack_date": d.get("AckDt"), "qr": bool(d.get("SignedQRCode"))}


def _save_qr(conn, ws: str, txn_id: int, qr: str) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hop_party_transactions)")}
    if "einv_qr" not in cols:
        conn.execute("ALTER TABLE hop_party_transactions ADD COLUMN einv_qr TEXT")
    conn.execute("UPDATE hop_party_transactions SET einv_qr=? WHERE workspace_id=? AND id=?", (qr or None, ws, txn_id))


def cancel_irn(conn: sqlite3.Connection, ws: str, txn_id: int, reason: str = "1", remark: str = "") -> dict[str, Any]:
    cfg = _cfg(conn, ws)
    r = conn.execute("SELECT irn FROM hop_party_transactions WHERE workspace_id=? AND id=?", (ws, int(txn_id))).fetchone()
    if not r or not r[0]:
        raise ValueError("This bill has no IRN")
    if str(reason) not in ("1", "2", "3", "4"):
        raise ValueError("Reason: 1 duplicate, 2 data entry mistake, 3 order cancelled, 4 other")
    d = _call(cfg, ws, "POST", "/eicore/v1.03/Invoice/Cancel", {"Irn": r[0], "CnlRsn": str(reason), "CnlRem": (remark or "Cancelled")[:100]})
    conn.execute("UPDATE hop_party_transactions SET irn=NULL, ack_no=NULL, ack_date=NULL, notes=trim(COALESCE(notes,'') || ?) "
                 "WHERE workspace_id=? AND id=?", (f" [IRN {r[0][:12]}… cancelled {d.get('CancelDate') or ''}]", ws, int(txn_id)))
    _save_qr(conn, ws, int(txn_id), "")
    conn.commit()
    return {"cancelled": True, "date": d.get("CancelDate")}


def eway_by_irn(conn: sqlite3.Connection, ws: str, txn_id: int, transport: dict[str, Any]) -> dict[str, Any]:
    cfg = _cfg(conn, ws)
    r = conn.execute("SELECT irn FROM hop_party_transactions WHERE workspace_id=? AND id=?", (ws, int(txn_id))).fetchone()
    if not r or not r[0]:
        raise ValueError("Make the IRN first")
    body = {"Irn": r[0], "Distance": int(transport.get("distance") or 0), "TransMode": str(transport.get("mode") or "1"),
            "TransId": transport.get("transporter_gstin") or None, "TransName": transport.get("transporter_name") or None,
            "VehNo": (transport.get("vehicle_no") or "").replace(" ", "").upper() or None, "VehType": "R"}
    d = _call(cfg, ws, "POST", "/eiewb/v1.03/ewaybill", body)
    conn.execute("UPDATE hop_party_transactions SET eway_bill_no=? WHERE workspace_id=? AND id=?", (str(d.get("EwbNo") or ""), ws, int(txn_id)))
    conn.commit()
    return {"eway_bill_no": d.get("EwbNo"), "valid_till": d.get("EwbValidTill")}


def gstin_details(conn: sqlite3.Connection, ws: str, gstin: str) -> dict[str, Any] | None:
    """Live GSTIN details when the IRP login is on; None otherwise (caller falls back)."""
    from app import hop_gov

    cfg = hop_gov.live(conn, ws, "einvoice")
    if not cfg:
        return None
    d = _call(cfg, ws, "GET", f"/eivital/v1.03/Master/gstin/{gstin}")
    return {"gstin": d.get("Gstin"), "legal_name": d.get("LegalName"), "trade_name": d.get("TradeName"),
            "status": d.get("Status"), "address": " ".join(str(d.get(k) or "") for k in ("AddrBnm", "AddrBno", "AddrFlno", "AddrSt", "AddrLoc")).strip(),
            "state_code": d.get("StateCode"), "pincode": d.get("AddrPncd"), "source": "GST portal (IRP)"}


def test_keys(conn: sqlite3.Connection, ws: str) -> tuple[bool, str]:
    from app import hop_gov

    cfg = hop_gov.config(conn, ws, "einvoice")
    try:
        auth(cfg, ws, force=True)
        return True, "Logged in to the IRP" + (" sandbox" if cfg.get("mode") == "test" else "")
    except (ValueError, KeyError) as exc:
        return False, str(exc)

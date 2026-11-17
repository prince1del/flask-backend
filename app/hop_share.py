"""HoP sharing: UPI payment link on bills, WhatsApp message from the owner's template.

* ``upi_link`` — a standard ``upi://pay`` link (any UPI app opens it with amount filled).
* ``share_payload`` — the message for a bill (Settings → Sharing template), the party's
  WhatsApp number and a ready ``wa.me`` link. Nothing is sent by the app itself:
  the user taps Send in WhatsApp.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any
from urllib.parse import quote, urlencode

UPI_RE = re.compile(r"^[\w.\-]{2,256}@[A-Za-z][A-Za-z0-9]{1,63}$")
PLACEHOLDERS = ("party", "doc", "number", "date", "amount", "due", "due_date", "firm", "upi_link")
DEFAULT_TEMPLATE = ("Dear {party}, thank you for your business. {doc} {number} dated {date} for ₹{amount}."
                    " Balance due ₹{due}.{upi_line} — {firm}")


def valid_upi(upi_id: str) -> bool:
    return bool(UPI_RE.match(str(upi_id or "").strip()))


def upi_link(upi_id: str, payee: str, amount: float, note: str = "") -> str:
    q = {"pa": upi_id.strip(), "pn": (payee or "").strip()[:40], "cu": "INR"}
    if amount and amount > 0:
        q["am"] = f"{amount:.2f}"
    if note:
        q["tn"] = note[:40]
    return "upi://pay?" + urlencode(q, quote_via=quote)


def _money(v: Any) -> str:
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        n = 0.0
    s = f"{n:,.2f}"
    return s[:-3] if s.endswith(".00") else s


def _digits(phone: str) -> str:
    d = re.sub(r"\D", "", str(phone or ""))
    if len(d) == 10:
        d = "91" + d
    if len(d) == 11 and d.startswith("0"):
        d = "91" + d[1:]
    return d if 11 <= len(d) <= 15 else ""


def _firm(conn, ws) -> dict[str, Any]:
    try:
        r = conn.execute("SELECT * FROM hop_firm_profile WHERE workspace_id=? LIMIT 1", (ws,)).fetchone()
        return dict(r) if r else {}
    except sqlite3.OperationalError:
        return {}


def bill_upi(conn: sqlite3.Connection, ws: str, txn: dict[str, Any]) -> dict[str, Any] | None:
    """UPI block for a sale invoice with money still due (Settings → Sharing)."""
    from app import hop_app_settings

    s = hop_app_settings.get_all(conn, ws)
    upi_id = str(s.get("upi_id") or "").strip()
    if not upi_id or not s.get("print_upi") or int(txn.get("txn_type") or 0) not in (1, 83):
        return None
    due = float(txn.get("balance_amount") if txn.get("balance_amount") is not None else txn.get("total_amount") or 0)
    if due <= 0.009:
        return None
    payee = s.get("upi_name") or _firm(conn, ws).get("firm_name") or ""
    link = upi_link(upi_id, payee, due, f"{txn.get('txn_number') or ''}".strip())
    return {"upi_id": upi_id, "amount": round(due, 2), "link": link, "qr_png": qr_png_base64(link)}


def qr_png_base64(text: str) -> str | None:
    """QR picture (PNG, base64) for the UPI link — any UPI app scans it with the amount filled."""
    try:
        import base64
        import io

        import segno

        buf = io.BytesIO()
        segno.make(text, error="m").save(buf, kind="png", scale=6, border=2)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # library missing or text too long: the link still works
        return None


def fmt_date(ymd: str, style: str | None) -> str:
    """2026-11-18 → the owner's date format (Settings → Print): 18-11-2026, 18/11/2026, 18 Nov 2026 or as is."""
    try:
        y, m, d = ymd.split("-")
        mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][int(m) - 1]
    except (ValueError, IndexError):
        return ymd
    return {"dd/mm/yyyy": f"{d}/{m}/{y}", "yyyy-mm-dd": ymd, "dd mmm yyyy": f"{d} {mon} {y}"}.get(style or "", f"{d}-{m}-{y}")


def share_payload(conn: sqlite3.Connection, ws: str, party_txn_id: int, kind: str = "bill") -> dict[str, Any]:
    from app import hop_app_settings
    from app.hop_money import LABELS

    r = conn.execute("SELECT * FROM hop_party_transactions WHERE workspace_id=? AND id=?", (ws, int(party_txn_id))).fetchone()
    if not r:
        raise ValueError("Document not found")
    t = dict(r)
    s = hop_app_settings.get_all(conn, ws)
    phone = ""
    table = {"customer": "hop_customers", "vendor": "hop_vendors"}.get(t.get("party_type") or "")
    if table and t.get("party_id"):
        p = conn.execute(f"SELECT * FROM {table} WHERE workspace_id=? AND id=?", (ws, int(t["party_id"]))).fetchone()
        if p:
            p = dict(p)
            phone = p.get("whatsapp") or p.get("mobile") or p.get("phone") or ""
    upi = bill_upi(conn, ws, t)
    firm = _firm(conn, ws).get("firm_name") or ""
    values = {
        "party": t.get("party_name") or "", "doc": LABELS.get(int(t.get("txn_type") or 0), t.get("txn_label") or "Bill"),
        "number": t.get("txn_number") or "", "date": fmt_date((t.get("txn_date") or "")[:10], s.get("date_format")),
        "amount": _money(t.get("total_amount")), "due": _money(t.get("balance_amount")),
        "due_date": fmt_date((t.get("due_date") or "")[:10], s.get("date_format")), "firm": firm, "upi_link": upi["link"] if upi else "",
        "upi_line": f" Pay by UPI: {upi['link']}" if upi else "",
    }
    tpl = str(s.get("msg_reminder" if kind == "reminder" else "msg_bill") or "").strip() or DEFAULT_TEMPLATE
    text = re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), tpl)
    wa = _digits(phone)
    return {"text": text, "phone": wa, "upi": upi,
            "whatsapp_url": (f"https://wa.me/{wa}?text=" if wa else "https://wa.me/?text=") + quote(text)}

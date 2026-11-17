"""Online catalogue link (like Vyapar's online store): the owner shares one link; a customer opens it without a
login, sees the items (and prices, if the owner wants), picks quantities and sends an order → it becomes a
Sale Order in NEXORA and the owner gets an email. Nothing is billed by itself — the owner checks and converts.

Safety: the link has a long random code (can be switched off or changed any time); it shows only active items'
name / category / unit / price; an order is limited in size and the same phone / network cannot flood orders.
"""
from __future__ import annotations

import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

MAX_LINES = 50
MAX_QTY = 10000
ORDERS_PER_HOUR = 8            # per phone and per network address, per link
_recent: dict[str, deque] = defaultdict(deque)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS hop_catalog_links (
        workspace_id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        show_prices INTEGER NOT NULL DEFAULT 1, hide_out_of_stock INTEGER NOT NULL DEFAULT 0,
        message TEXT, orders INTEGER NOT NULL DEFAULT 0, created_at TEXT, updated_at TEXT)""")


def _row(conn, ws: str) -> dict[str, Any] | None:
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM hop_catalog_links WHERE workspace_id=?", (ws,)).fetchone()
    return dict(r) if r else None


def get(conn: sqlite3.Connection, ws: str) -> dict[str, Any] | None:
    ensure_schema(conn)
    return _row(conn, ws)


def save(conn: sqlite3.Connection, ws: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Owner: make the link (first time), switch it on/off, new code, show prices, hide out-of-stock, message."""
    ensure_schema(conn)
    cur = _row(conn, ws)
    if cur is None:
        conn.execute("INSERT INTO hop_catalog_links (workspace_id, token, created_at, updated_at) VALUES (?,?,?,?)",
                     (ws, secrets.token_urlsafe(12), _now(), _now()))
    sets: dict[str, Any] = {}
    for k in ("active", "show_prices", "hide_out_of_stock"):
        if k in changes:
            sets[k] = 1 if changes[k] in (True, 1, "1", "true", "on") else 0
    if "message" in changes:
        sets["message"] = str(changes.get("message") or "").strip()[:300]
    if changes.get("new_code"):
        sets["token"] = secrets.token_urlsafe(12)       # the old link stops working at once
    if sets:
        sets["updated_at"] = _now()
        conn.execute(f"UPDATE hop_catalog_links SET {', '.join(f'{k}=?' for k in sets)} WHERE workspace_id=?",
                     (*sets.values(), ws))
    conn.commit()
    return _row(conn, ws)


def by_token(conn: sqlite3.Connection, token: str) -> dict[str, Any] | None:
    ensure_schema(conn)
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", token or ""):
        return None
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM hop_catalog_links WHERE token=? AND active=1", (token,)).fetchone()
    return dict(r) if r else None


def public_catalog(conn: sqlite3.Connection, link: dict[str, Any]) -> dict[str, Any]:
    from app import hop_firms

    ws = link["workspace_id"]
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, name, code, category, unit, selling_price, mrp, stock_qty, item_type, specs FROM hop_products
           WHERE workspace_id=? AND (deleted_at IS NULL OR trim(deleted_at)='') AND COALESCE(status,'active')='active'
           ORDER BY lower(COALESCE(category,'')), lower(name)""", (ws,)).fetchall()
    items = []
    for r in rows:
        goods = (r["item_type"] or "product") != "service"
        if link.get("hide_out_of_stock") and goods and float(r["stock_qty"] or 0) <= 0:
            continue
        it = {"id": r["id"], "name": r["name"], "code": r["code"] or "", "category": r["category"] or "Other",
              "unit": r["unit"] or "", "note": (r["specs"] or "")[:160]}
        if link.get("show_prices"):
            it["price"] = round(float(r["selling_price"] or 0), 2)
            if r["mrp"]:
                it["mrp"] = round(float(r["mrp"]), 2)
        items.append(it)
    prof = conn.execute("SELECT phone, address FROM hop_firm_profile WHERE workspace_id=?", (ws,)).fetchone()
    return {"firm": hop_firms.business_name(conn, ws, "Our catalogue"), "message": link.get("message") or "",
            "phone": (prof["phone"] if prof else "") or "", "address": (prof["address"] if prof else "") or "",
            "show_prices": bool(link.get("show_prices")), "items": items}


def _limited(key: str) -> bool:
    q, now = _recent[key], time.time()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= ORDERS_PER_HOUR:
        return True
    q.append(now)
    return False


def place_order(conn: sqlite3.Connection, link: dict[str, Any], body: dict[str, Any], ip: str = "") -> dict[str, Any]:
    """Customer's order → Sale Order (txn 65) in the owner's firm. Prices always come from NEXORA, never the page."""
    from app.hop_db import create_customer
    from app.hop_doc_preview import create_manual_party_document

    ws = link["workspace_id"]
    name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()[:80]
    phone = re.sub(r"\D", "", str(body.get("phone") or ""))[-10:]
    note = str(body.get("note") or "").strip()[:500]
    if len(name) < 2:
        raise ValueError("Please write your name")
    if len(phone) != 10:
        raise ValueError("Please write a 10-digit mobile number")
    raw = body.get("lines") or []
    if not isinstance(raw, list) or not raw:
        raise ValueError("Pick at least one item")
    if len(raw) > MAX_LINES:
        raise ValueError(f"At most {MAX_LINES} items in one order")
    if _limited(f"{link['token']}|{phone}") or (ip and _limited(f"{link['token']}|ip|{ip}")):
        raise ValueError("Too many orders from this number — please call the shop")
    conn.row_factory = sqlite3.Row
    lines = []
    for ln in raw:
        try:
            pid, qty = int(ln.get("id")), float(ln.get("qty"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not (0 < qty <= MAX_QTY):
            continue
        p = conn.execute("SELECT id, name, code, unit, selling_price, gst_pct, hsn FROM hop_products WHERE workspace_id=? "
                         "AND id=? AND (deleted_at IS NULL OR trim(deleted_at)='')", (ws, pid)).fetchone()
        if not p:
            continue
        lines.append({"item_name": p["name"], "item_code": p["code"] or "", "product_id": p["id"], "qty": qty,
                      "unit": p["unit"] or "", "rate": float(p["selling_price"] or 0), "tax_pct": float(p["gst_pct"] or 0),
                      "hsn": p["hsn"] or ""})
    if not lines:
        raise ValueError("Pick at least one item")
    cust = conn.execute("SELECT id FROM hop_customers WHERE workspace_id=? AND substr(replace(replace(COALESCE(mobile,''),' ',''),'+',''), -10)=?",
                        (ws, phone)).fetchone()
    cid = int(cust["id"]) if cust else int(create_customer(conn, ws, {"company": name, "contact_person": name, "mobile": phone,
                                                                         "source": "Online catalogue"})["id"])
    doc = create_manual_party_document(conn, ws, {
        "txn_type": 65, "customer_id": cid, "txn_date": datetime.now().date().isoformat(), "lines": lines,
        "notes": f"Online order from the catalogue link — {name}, {phone}" + (f". Note: {note}" if note else "")})
    conn.execute("UPDATE hop_catalog_links SET orders=orders+1, updated_at=? WHERE workspace_id=?", (_now(), ws))
    conn.commit()
    _tell_owner(conn, ws, name, phone, doc, lines, note)
    return {"number": doc.get("txn_number") or "", "items": len(lines)}


def _tell_owner(conn, ws, name, phone, doc, lines, note) -> None:
    try:
        from app import hop_firms, hop_reminders
        from app.services.outbound_mail import send_plain_email, smtp_configured

        to = hop_reminders._owner_email(conn, ws)
        if not (to and smtp_configured()):
            return
        firm = hop_firms.business_name(conn, ws, "your business")
        body = [f"New online order for {firm} from {name} ({phone}) — {doc.get('txn_number') or ''}", ""]
        body += [f"  • {ln['item_name']} × {ln['qty']:g} {ln['unit']}".rstrip() for ln in lines[:30]]
        if note:
            body += ["", f"Note: {note}"]
        body += ["", "It is saved as a Sale Order in NEXORA — check it and convert it to an invoice."]
        send_plain_email(to=to, subject=f"NEXORA — new online order from {name}", body="\n".join(body))
    except Exception:
        pass


def summary_for_ai(conn: sqlite3.Connection, ws: str) -> dict[str, Any]:
    link = get(conn, ws)
    rows = conn.execute("SELECT txn_number, party_name, txn_date, total_amount FROM hop_party_transactions WHERE workspace_id=? "
                        "AND txn_type=65 AND notes LIKE 'Online order from the catalogue link%' ORDER BY id DESC LIMIT 15",
                        (ws,)).fetchall()
    return {"link_on": bool(link and link.get("active")), "orders_total": (link or {}).get("orders", 0),
            "recent_online_orders": [{"number": r[0], "party": r[1], "date": r[2], "amount": r[3]} for r in rows],
            "note": "Share the link from Catalogue → Share online catalogue. Orders arrive as Sale Orders."}

"""Service reminders (business UI, like Vyapar's).

An item can have a service period ("service every 180 days" — AC, RO purifier, generator, bike…).
Every sale invoice line of such an item starts a cycle: service is due ``period`` days after the sale,
and again ``period`` days after each service the owner marks done. The list groups services as
overdue / due today / due soon, each with a ready WhatsApp text for the customer.

Nothing is sent by the app itself: the owner taps WhatsApp (noted here) or "Done" (next cycle starts).
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

SOON_DAYS = 7          # "due soon" window
OVERDUE_WINDOW = 90    # older missed services drop off the list (owner can still mark them done)
DEFAULT_MSG = ("Dear {party}, your {item} (bought on {date}) is due for service on {due_date}. "
               "Reply here to book a visit. — {firm}")

# Rule-based hint for the item form (no AI cost): typical service gaps in days.
_HINTS = (
    (r"\b(ac|a\.c\.|air ?condition\w*|split|inverter ac)\b", 180),
    (r"\b(ro|purifier|water filter|uv)\b", 90),
    (r"\b(generator|genset|dg set)\b", 90),
    (r"\b(bike|scooter|motorcycle|activa)\b", 90),
    (r"\b(car|vehicle)\b", 180),
    (r"\b(inverter|ups|battery)\b", 180),
    (r"\b(chimney|geyser|water heater)\b", 180),
    (r"\b(fire extinguisher)\b", 365),
    (r"\b(washing machine|refrigerator|fridge|cooler)\b", 365),
    (r"\b(pest control)\b", 90),
)


def suggest_days(name: str) -> int | None:
    n = (name or "").lower()
    for pat, days in _HINTS:
        if re.search(pat, n):
            return days
    return None


def _today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def ensure_schema(conn: sqlite3.Connection) -> None:
    from app import hop_items

    hop_items.ensure_columns(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_service_log (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               workspace_id TEXT NOT NULL,
               line_id INTEGER NOT NULL,       -- hop_txn_lines.id (the sold item)
               action TEXT NOT NULL,           -- done / whatsapp
               done_on TEXT,                   -- day the service was done (for 'done')
               at TEXT NOT NULL
           )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_hop_service_log ON hop_service_log(workspace_id, line_id)")


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat((s or "")[:10]) if s else None
    except ValueError:
        return None


def _rows(conn: sqlite3.Connection, ws: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """SELECT l.id AS line_id, l.item_name, l.qty, t.id AS txn_id, t.txn_number, t.txn_date,
                  t.party_id, t.party_name, p.id AS product_id, p.name AS product_name, p.service_period_days AS period
           FROM hop_txn_lines l
           JOIN hop_party_transactions t ON t.source_txn_id = l.source_txn_id AND t.workspace_id = l.workspace_id
           JOIN hop_products p ON p.workspace_id = l.workspace_id
                AND (p.id = l.product_id OR (l.product_id IS NULL AND lower(trim(p.name)) = lower(trim(l.item_name))))
           WHERE l.workspace_id=? AND t.txn_type=1
             AND (t.deleted_at IS NULL OR trim(t.deleted_at)='')
             AND (p.deleted_at IS NULL OR trim(p.deleted_at)='')
             AND COALESCE(p.service_period_days, 0) > 0""", (ws,)).fetchall()


def _customer(conn, ws: str, party_id) -> dict[str, str]:
    if not party_id:
        return {}
    r = conn.execute("SELECT * FROM hop_customers WHERE workspace_id=? AND id=?", (ws, int(party_id))).fetchone()
    return dict(r) if r else {}


def due_list(conn: sqlite3.Connection, ws: str, today: date | None = None) -> dict[str, Any]:
    from app import hop_app_settings, hop_firms
    from app.hop_share import _digits

    ensure_schema(conn)
    today = today or _today()
    firm = hop_firms.business_name(conn, ws, "Your business")
    tmpl = (hop_app_settings.get_all(conn, ws).get("msg_service") or "").strip() or DEFAULT_MSG
    out: dict[str, list] = {"overdue": [], "today": [], "soon": []}
    seen: set[int] = set()
    for r in _rows(conn, ws):
        if r["line_id"] in seen:   # a line matched by id and by name: count once
            continue
        seen.add(r["line_id"])
        sold = _d(r["txn_date"])
        period = int(r["period"] or 0)
        if not sold or period <= 0:
            continue
        logs = conn.execute("SELECT action, done_on, at FROM hop_service_log WHERE workspace_id=? AND line_id=? ORDER BY id",
                            (ws, r["line_id"])).fetchall()
        done = [_d(x[1]) for x in logs if x[0] == "done" and _d(x[1])]
        base = max([sold, *done])
        due = base + timedelta(days=period)
        days = (due - today).days
        if days < -OVERDUE_WINDOW:
            continue
        group = "overdue" if days < 0 else "today" if days == 0 else "soon" if days <= SOON_DAYS else None
        if not group:
            continue
        cust = _customer(conn, ws, r["party_id"])
        phone = _digits(cust.get("whatsapp") or cust.get("mobile") or cust.get("phone") or "")
        item = r["item_name"] or r["product_name"]
        text = (tmpl.replace("{party}", r["party_name"] or "Sir/Madam").replace("{item}", item or "item")
                .replace("{date}", sold.strftime("%d %b %Y")).replace("{due_date}", due.strftime("%d %b %Y"))
                .replace("{number}", r["txn_number"] or "").replace("{firm}", firm))
        cycle_start = (base.isoformat())
        reminded = next((x[2] for x in reversed(logs) if x[0] == "whatsapp" and (x[2] or "")[:10] >= cycle_start), None)
        out[group].append({
            "line_id": r["line_id"], "txn_id": r["txn_id"], "number": r["txn_number"], "party": r["party_name"],
            "item": item, "sold_on": sold.isoformat(), "due_date": due.isoformat(), "days": days,
            "period_days": period, "services_done": len(done), "phone": phone, "text": text,
            "whatsapp_url": (f"https://wa.me/{phone}?text=" if phone else "https://wa.me/?text=") + quote(text),
            "last_reminded": reminded,
        })
    for v in out.values():
        v.sort(key=lambda x: x["days"])
    return {**out, "counts": {k: len(v) for k, v in out.items()}, "as_of": today.isoformat(), "soon_days": SOON_DAYS}


def mark(conn: sqlite3.Connection, ws: str, line_id: int, action: str, done_on: str | None = None) -> dict[str, Any]:
    """action: done (service finished — next one counts from done_on, default today) or whatsapp (reminder sent)."""
    ensure_schema(conn)
    if action not in ("done", "whatsapp"):
        raise ValueError("Unknown action")
    ok = conn.execute("SELECT 1 FROM hop_txn_lines WHERE workspace_id=? AND id=?", (ws, int(line_id))).fetchone()
    if not ok:
        raise ValueError("Sold item not found")
    day = None
    if action == "done":
        d = _d(done_on) if done_on else _today()
        if d is None:
            raise ValueError("Date must be YYYY-MM-DD")
        if d > _today():
            raise ValueError("Service date cannot be in the future")
        day = d.isoformat()
    conn.execute("INSERT INTO hop_service_log (workspace_id, line_id, action, done_on, at) VALUES (?,?,?,?,?)",
                 (ws, int(line_id), action, day, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return {"line_id": int(line_id), "action": action, "done_on": day}


def summary_for_ai(conn: sqlite3.Connection, ws: str, q: str = "") -> dict[str, Any]:
    dl = due_list(conn, ws)
    q = (q or "").strip().lower()
    slim = lambda xs: [{k: x[k] for k in ("party", "item", "due_date", "days", "sold_on", "phone")}  # noqa: E731
                       for x in xs if not q or q in (x["party"] or "").lower() or q in (x["item"] or "").lower()][:20]
    items = conn.execute("SELECT name, service_period_days FROM hop_products WHERE workspace_id=? "
                         "AND COALESCE(service_period_days,0)>0 AND (deleted_at IS NULL OR trim(deleted_at)='') LIMIT 30",
                         (ws,)).fetchall()
    return {"service_overdue": slim(dl["overdue"]), "service_today": slim(dl["today"]),
            "service_soon": slim(dl["soon"]), "as_of": dl["as_of"],
            "items_with_service_period": [{"item": r[0], "every_days": r[1]} for r in items],
            "note": "Set 'Service every (days)' on an item to get service reminders after each sale."}

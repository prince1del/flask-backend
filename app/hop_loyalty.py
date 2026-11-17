"""HoP loyalty points: customers earn points on sale invoices and use them as a discount on a later bill.

Earned points are worked out from the sale invoices themselves (from the day loyalty was turned on), so
editing or deleting a bill always keeps points right. Only redemptions are stored (hop_loyalty_redeem),
one row per bill; a deleted bill's redemption no longer counts.
Settings → Party: loyalty_on, loyalty_earn_per (₹ per point), loyalty_point_value (₹ per point used).
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_loyalty_redeem (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               workspace_id TEXT NOT NULL,
               customer_id INTEGER NOT NULL,
               party_txn_id INTEGER NOT NULL,
               points REAL NOT NULL,
               value REAL NOT NULL,
               created_at TEXT
           )""")


def settings(conn: sqlite3.Connection, ws: str) -> dict[str, Any]:
    from app import hop_app_settings

    s = hop_app_settings.get_all(conn, ws)
    return {"on": bool(s.get("loyalty_on")), "earn_per": float(s.get("loyalty_earn_per") or 100),
            "point_value": float(s.get("loyalty_point_value") or 1), "from": str(s.get("loyalty_from") or "")}


def _live(t: str = "t") -> str:
    return f"({t}.deleted_at IS NULL OR trim({t}.deleted_at)='')"


def balance(conn: sqlite3.Connection, ws: str, customer_id: int, exclude_txn: int | None = None) -> dict[str, Any]:
    """Points earned, used and left for one customer (a bill being edited can leave out its own use)."""
    ensure_schema(conn)
    cfg = settings(conn, ws)
    if not cfg["on"]:
        return {"on": False, "earned": 0, "used": 0, "left": 0, "point_value": cfg["point_value"], "value_left": 0.0}
    rows = conn.execute(
        f"SELECT total_amount FROM hop_party_transactions t WHERE t.workspace_id=? AND t.txn_type=1 AND t.party_type='customer' "
        f"AND t.party_id=? AND {_live()} AND date(t.txn_date) >= date(?)", (ws, int(customer_id), cfg["from"] or "1900-01-01")).fetchall()
    earned = sum(math.floor(float(r[0] or 0) / cfg["earn_per"]) for r in rows) if cfg["earn_per"] > 0 else 0
    q = (f"SELECT COALESCE(SUM(r.points),0) FROM hop_loyalty_redeem r JOIN hop_party_transactions t "
         f"ON t.workspace_id=r.workspace_id AND t.id=r.party_txn_id WHERE r.workspace_id=? AND r.customer_id=? AND {_live()}")
    args: list[Any] = [ws, int(customer_id)]
    if exclude_txn:
        q += " AND r.party_txn_id<>?"
        args.append(int(exclude_txn))
    used = float(conn.execute(q, args).fetchone()[0] or 0)
    left = max(0.0, earned - used)
    return {"on": True, "earned": earned, "used": used, "left": left, "point_value": cfg["point_value"],
            "value_left": round(left * cfg["point_value"], 2), "earn_per": cfg["earn_per"]}


def apply_redeem(conn: sqlite3.Connection, ws: str, txn_type: int, party_id: int, payload: dict[str, Any],
                 sub_total: float, txn_id: int | None = None) -> None:
    """Before a sale invoice's totals are worked out: points used → added to the bill discount (₹)."""
    pts = float(payload.get("redeem_points") or 0)
    if pts <= 0:
        payload["_loyalty"] = None
        return
    if int(txn_type) != 1 or not party_id:
        raise ValueError("Loyalty points can be used only on a sale invoice to a customer.")
    if pts != int(pts):
        raise ValueError("Use whole points.")
    bal = balance(conn, ws, party_id, exclude_txn=txn_id)
    if not bal["on"]:
        raise ValueError("Loyalty points are off — turn them on in Settings → Party.")
    if pts > bal["left"]:
        raise ValueError(f"This customer has only {bal['left']:g} points.")
    value = round(pts * bal["point_value"], 2)
    disc = float(payload.get("discount_amount") or 0)
    pct = float(payload.get("discount_pct") or 0)
    if disc <= 0.009 and pct > 0.009:
        disc = round(sub_total * pct / 100.0, 2)
    if value > sub_total - disc + 0.001:
        raise ValueError("Points used are worth more than the bill.")
    payload["discount_amount"] = round(disc + value, 2)
    payload["discount_pct"] = 0
    payload["_loyalty"] = {"points": pts, "value": value, "customer_id": int(party_id)}


def record(conn: sqlite3.Connection, ws: str, txn_id: int, payload: dict[str, Any]) -> None:
    """After the bill is saved: keep one redemption row for it (replaced on edit)."""
    ensure_schema(conn)
    conn.execute("DELETE FROM hop_loyalty_redeem WHERE workspace_id=? AND party_txn_id=?", (ws, int(txn_id)))
    info = payload.get("_loyalty")
    if info:
        conn.execute("INSERT INTO hop_loyalty_redeem (workspace_id, customer_id, party_txn_id, points, value, created_at) "
                     "VALUES (?,?,?,?,?,?)", (ws, info["customer_id"], int(txn_id), info["points"], info["value"], _now()))


def used_on_bill(conn: sqlite3.Connection, ws: str, txn_id: int) -> dict[str, Any] | None:
    ensure_schema(conn)
    r = conn.execute("SELECT points, value FROM hop_loyalty_redeem WHERE workspace_id=? AND party_txn_id=?",
                     (ws, int(txn_id))).fetchone()
    return {"points": r[0], "value": r[1]} if r else None


def top_customers(conn: sqlite3.Connection, ws: str, q: str = "", limit: int = 30) -> dict[str, Any]:
    """Points per customer (Ask Nexora and the Loyalty screen)."""
    ensure_schema(conn)
    cfg = settings(conn, ws)
    if not cfg["on"]:
        return {"on": False, "customers": [], "note": "Loyalty points are off (Settings → Party)."}
    ids = conn.execute(
        f"SELECT DISTINCT t.party_id, t.party_name FROM hop_party_transactions t WHERE t.workspace_id=? AND t.txn_type=1 "
        f"AND t.party_type='customer' AND {_live()} AND date(t.txn_date) >= date(?)", (ws, cfg["from"] or "1900-01-01")).fetchall()
    out = []
    for pid, name in ids:
        if q and q.lower() not in (name or "").lower():
            continue
        b = balance(conn, ws, int(pid))
        if b["earned"] or b["used"]:
            out.append({"customer_id": pid, "customer": name, **{k: b[k] for k in ("earned", "used", "left", "value_left")}})
    out.sort(key=lambda r: -r["left"])
    return {"on": True, "earn_per": cfg["earn_per"], "point_value": cfg["point_value"], "since": cfg["from"],
            "customers": out[:limit]}

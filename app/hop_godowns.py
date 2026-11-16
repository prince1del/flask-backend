"""HoP godowns (stores / warehouses): stock per godown, transfers, godown on bills.

* A "Main godown" always exists (the default). Stock not tied to any godown sits there.
* Bills carry ``godown_id`` (blank = main); their stock moves are tagged with it.
* A transfer moves stock between godowns; the item's total stock does not change.
Godown stock = the stock journals tagged to that godown; the main godown holds the rest.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS hop_godowns (
        id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, name TEXT NOT NULL,
        address TEXT, is_default INTEGER DEFAULT 0, created_at TEXT NOT NULL, UNIQUE(workspace_id, name))""")
    for table in ("hop_stock_journals", "hop_party_transactions"):
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "godown_id" not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN godown_id INTEGER")


def default_id(conn: sqlite3.Connection, ws: str) -> int:
    ensure_schema(conn)
    r = conn.execute("SELECT id FROM hop_godowns WHERE workspace_id=? AND is_default=1 LIMIT 1", (ws,)).fetchone()
    if r:
        return int(r[0])
    cur = conn.execute("INSERT OR IGNORE INTO hop_godowns (workspace_id, name, is_default, created_at) VALUES (?, 'Main godown', 1, ?)",
                       (ws, _now()))
    if cur.lastrowid:
        return int(cur.lastrowid)
    return int(conn.execute("SELECT id FROM hop_godowns WHERE workspace_id=? AND name='Main godown'", (ws,)).fetchone()[0])


def check(conn: sqlite3.Connection, ws: str, godown_id: Any) -> int | None:
    """Godown id for a bill (None = main). Raises for an unknown godown."""
    if godown_id in (None, "", 0, "0"):
        return None
    ensure_schema(conn)
    gid = int(godown_id)
    r = conn.execute("SELECT is_default FROM hop_godowns WHERE workspace_id=? AND id=?", (ws, gid)).fetchone()
    if not r:
        raise ValueError("Godown not found")
    return None if r[0] else gid


def list_godowns(conn: sqlite3.Connection, ws: str) -> list[dict[str, Any]]:
    main = default_id(conn, ws)
    rows = [dict(r) for r in conn.execute("SELECT id, name, address, is_default FROM hop_godowns WHERE workspace_id=? "
                                          "ORDER BY is_default DESC, lower(name)", (ws,))]
    values = stock_by_godown(conn, ws)
    for g in rows:
        g["items"] = sum(1 for v in values if abs(v["by_godown"].get(g["id"], 0)) > 1e-9)
        g["stock_value"] = round(sum(v["by_godown"].get(g["id"], 0) * v["purchase_price"] for v in values), 2)
    conn.commit()
    _ = main
    return rows


def create(conn: sqlite3.Connection, ws: str, name: str, address: str = "") -> dict[str, Any]:
    default_id(conn, ws)
    name = str(name or "").strip()[:60]
    if not name:
        raise ValueError("Godown name is required")
    if conn.execute("SELECT 1 FROM hop_godowns WHERE workspace_id=? AND lower(name)=lower(?)", (ws, name)).fetchone():
        raise ValueError("A godown with this name already exists")
    cur = conn.execute("INSERT INTO hop_godowns (workspace_id, name, address, is_default, created_at) VALUES (?, ?, ?, 0, ?)",
                       (ws, name, str(address or "").strip()[:200], _now()))
    conn.commit()
    return {"id": int(cur.lastrowid), "name": name}


def stock_by_godown(conn: sqlite3.Connection, ws: str, product_id: int | None = None) -> list[dict[str, Any]]:
    main = default_id(conn, ws)
    sql = ("SELECT id, name, unit, stock_qty, purchase_price FROM hop_products WHERE workspace_id=? "
           "AND (deleted_at IS NULL OR trim(deleted_at)='') AND COALESCE(item_type,'product')!='service'")
    params: list[Any] = [ws]
    if product_id:
        sql += " AND id=?"
        params.append(int(product_id))
    prods = [dict(r) for r in conn.execute(sql + " ORDER BY lower(name)", params)]
    tagged: dict[int, dict[int, float]] = {}
    for pid, gid, q in conn.execute("SELECT product_id, godown_id, SUM(qty_delta) FROM hop_stock_journals WHERE workspace_id=? "
                                    "AND godown_id IS NOT NULL AND godown_id!=? GROUP BY product_id, godown_id", (ws, main)):
        tagged.setdefault(int(pid), {})[int(gid)] = float(q or 0)
    out = []
    for p in prods:
        by = dict(tagged.get(int(p["id"]), {}))
        by[main] = round(float(p["stock_qty"] or 0) - sum(by.values()), 4)
        out.append({"product_id": p["id"], "name": p["name"], "unit": p.get("unit") or "", "total": float(p["stock_qty"] or 0),
                    "purchase_price": float(p["purchase_price"] or 0), "by_godown": {k: round(v, 4) for k, v in by.items()}})
    return out


def transfer(conn: sqlite3.Connection, ws: str, product_id: int, from_id: int, to_id: int, qty: float,
             date: str = "", note: str = "") -> dict[str, Any]:
    main = default_id(conn, ws)
    qty = float(qty or 0)
    if qty <= 0:
        raise ValueError("Quantity must be more than 0")
    if int(from_id) == int(to_id):
        raise ValueError("Choose two different godowns")
    for g in (from_id, to_id):
        if not conn.execute("SELECT 1 FROM hop_godowns WHERE workspace_id=? AND id=?", (ws, int(g))).fetchone():
            raise ValueError("Godown not found")
    row = stock_by_godown(conn, ws, int(product_id))
    if not row:
        raise ValueError("Item not found (services have no stock)")
    have = row[0]["by_godown"].get(int(from_id), 0.0)
    if have + 1e-9 < qty:
        raise ValueError(f"Only {have:g} {row[0]['unit']} of {row[0]['name']} in that godown")
    ref = f"transfer:{_now()}"
    day = (date or _now())[:10]
    for gid, delta in ((int(from_id), -qty), (int(to_id), qty)):
        conn.execute("INSERT INTO hop_stock_journals (workspace_id, product_id, entry_date, qty_delta, reason, reference, "
                     "created_at, godown_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (ws, int(product_id), day, delta, ("Godown transfer " + note).strip()[:120], ref, _now(),
                      None if gid == main else gid))
    # Moves out of / into the main godown change only the tagged side; the item total stays the same.
    conn.commit()
    return {"ok": True, "product": row[0]["name"], "qty": qty}

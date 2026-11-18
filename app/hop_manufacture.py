"""HoP Manufacturing: raw-material list per finished item (bill of materials) and "Manufacture" entries.

A manufacture entry uses up raw materials (stock down) and adds the finished item (stock up), with the
cost per piece = raw material cost (purchase price) + extra cost (labour, electricity…) ÷ quantity.
Undo puts every stock movement back. All stock changes go through hop_stock_journals so reports follow.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS hop_bom (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            raw_product_id INTEGER NOT NULL,
            qty REAL NOT NULL,
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_hop_bom ON hop_bom(workspace_id, product_id);
        CREATE TABLE IF NOT EXISTS hop_manufacture (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            entry_date TEXT,
            product_id INTEGER NOT NULL,
            product_name TEXT,
            qty REAL NOT NULL,
            raw_cost REAL DEFAULT 0,
            extra_cost REAL DEFAULT 0,
            unit_cost REAL DEFAULT 0,
            notes TEXT,
            created_at TEXT,
            deleted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS hop_manufacture_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            manufacture_id INTEGER NOT NULL,
            raw_product_id INTEGER NOT NULL,
            raw_name TEXT,
            qty REAL NOT NULL,
            cost REAL DEFAULT 0
        );
        """
    )


def _product(conn: sqlite3.Connection, ws: str, pid: int) -> dict[str, Any]:
    r = conn.execute("SELECT id, name, stock_qty, purchase_price, unit FROM hop_products WHERE workspace_id=? AND id=?",
                     (ws, int(pid))).fetchone()
    if not r:
        raise ValueError("Item not found")
    return {"id": r[0], "name": r[1], "stock_qty": _f(r[2]), "purchase_price": _f(r[3]), "unit": r[4] or ""}


def get_bom(conn: sqlite3.Connection, ws: str, product_id: int) -> dict[str, Any]:
    ensure_schema(conn)
    item = _product(conn, ws, product_id)
    rows = conn.execute(
        "SELECT b.raw_product_id, b.qty, p.name, p.unit, p.stock_qty, p.purchase_price FROM hop_bom b "
        "LEFT JOIN hop_products p ON p.workspace_id=b.workspace_id AND p.id=b.raw_product_id "
        "WHERE b.workspace_id=? AND b.product_id=? ORDER BY b.id", (ws, int(product_id))).fetchall()
    raw = [{"raw_product_id": r[0], "qty": _f(r[1]), "name": r[2] or "(deleted item)", "unit": r[3] or "",
            "stock_qty": _f(r[4]), "purchase_price": _f(r[5])} for r in rows]
    return {"product": item, "raw": raw,
            "cost_per_piece": round(sum(x["qty"] * x["purchase_price"] for x in raw), 2)}


def save_bom(conn: sqlite3.Connection, ws: str, product_id: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    ensure_schema(conn)
    _product(conn, ws, product_id)
    clean: dict[int, float] = {}
    for r in rows or []:
        rid = int(r.get("raw_product_id") or 0)
        qty = _f(r.get("qty"))
        if not rid:
            continue
        if rid == int(product_id):
            raise ValueError("An item cannot be its own raw material.")
        if qty <= 0:
            raise ValueError("Raw material quantity must be more than 0.")
        _product(conn, ws, rid)
        clean[rid] = clean.get(rid, 0.0) + qty
    # A raw material whose own list contains this item would loop forever.
    for rid in clean:
        if conn.execute("SELECT 1 FROM hop_bom WHERE workspace_id=? AND product_id=? AND raw_product_id=?",
                        (ws, rid, int(product_id))).fetchone():
            raise ValueError("That raw material is itself made from this item — please check the list.")
    conn.execute("DELETE FROM hop_bom WHERE workspace_id=? AND product_id=?", (ws, int(product_id)))
    for rid, qty in clean.items():
        conn.execute("INSERT INTO hop_bom (workspace_id, product_id, raw_product_id, qty, created_at) VALUES (?,?,?,?,?)",
                     (ws, int(product_id), rid, qty, _now()))
    conn.commit()
    return get_bom(conn, ws, product_id)


def plan(conn: sqlite3.Connection, ws: str, product_id: int, qty: Any, extra_cost: Any = 0) -> dict[str, Any]:
    """What making `qty` pieces needs: each raw material, stock, short by, cost — and warnings."""
    q = _f(qty)
    if q <= 0:
        raise ValueError("Quantity to make must be more than 0.")
    bom = get_bom(conn, ws, product_id)
    if not bom["raw"]:
        raise ValueError("Add the raw materials for this item first.")
    need, warnings = [], []
    raw_cost = 0.0
    for r in bom["raw"]:
        req = round(r["qty"] * q, 3)
        cost = round(req * r["purchase_price"], 2)
        raw_cost += cost
        short = round(max(0.0, req - r["stock_qty"]), 3)
        need.append({**r, "needed": req, "short": short, "cost": cost})
        if short > 0:
            warnings.append(f"{r['name']}: need {req:g} {r['unit']}, only {r['stock_qty']:g} in stock (short {short:g}).".replace("  ", " "))
        if r["purchase_price"] <= 0:
            warnings.append(f"{r['name']} has no purchase price — its cost is counted as 0.")
    extra = _f(extra_cost)
    unit_cost = round((raw_cost + extra) / q, 2)
    return {"product": bom["product"], "qty": q, "raw": need, "raw_cost": round(raw_cost, 2), "extra_cost": extra,
            "unit_cost": unit_cost, "warnings": warnings, "can_make_now": max_makeable(bom["raw"])}


def max_makeable(raw: list[dict[str, Any]]) -> float:
    vals = [r["stock_qty"] / r["qty"] for r in raw if r["qty"] > 0]
    return float(int(max(0.0, min(vals)))) if vals else 0.0


def _journal(conn, ws, pid, delta, reason, ref, date):
    conn.execute("INSERT INTO hop_stock_journals (workspace_id, product_id, entry_date, qty_delta, reason, reference, created_at) "
                 "VALUES (?,?,?,?,?,?,?)", (ws, pid, date, delta, reason, ref, _now()))
    conn.execute("UPDATE hop_products SET stock_qty = COALESCE(stock_qty,0) + ?, updated_at=? WHERE workspace_id=? AND id=?",
                 (delta, _now(), ws, pid))


def manufacture(conn: sqlite3.Connection, ws: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(conn)
    pid = int(payload.get("product_id") or 0)
    p = plan(conn, ws, pid, payload.get("qty"), payload.get("extra_cost"))
    short = [r for r in p["raw"] if r["short"] > 0]
    if short:
        try:
            from app import hop_app_settings

            mode = hop_app_settings.get_all(conn, ws).get("short_stock") or "warn"
        except Exception:
            mode = "warn"
        if mode == "block":
            raise ValueError("Not enough raw material: " + "; ".join(p["warnings"][:3]))
    date = str(payload.get("entry_date") or "")[:10] or __import__("app.hop_time", fromlist=["x"]).today_iso()
    cur = conn.execute(
        "INSERT INTO hop_manufacture (workspace_id, entry_date, product_id, product_name, qty, raw_cost, extra_cost, unit_cost, notes, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ws, date, pid, p["product"]["name"], p["qty"], p["raw_cost"], p["extra_cost"], p["unit_cost"],
         str(payload.get("notes") or "").strip()[:500], _now()))
    mid = int(cur.lastrowid)
    ref = f"MFG-{mid}"
    for r in p["raw"]:
        conn.execute("INSERT INTO hop_manufacture_lines (workspace_id, manufacture_id, raw_product_id, raw_name, qty, cost) VALUES (?,?,?,?,?,?)",
                     (ws, mid, r["raw_product_id"], r["name"], r["needed"], r["cost"]))
        _journal(conn, ws, r["raw_product_id"], -r["needed"], f"Used to make {p['product']['name']}", ref, date)
    _journal(conn, ws, pid, p["qty"], "Manufactured", ref, date)
    # First time made: the cost per piece becomes the item's purchase (cost) price, so profit reports work.
    if p["product"]["purchase_price"] <= 0 and p["unit_cost"] > 0:
        conn.execute("UPDATE hop_products SET purchase_price=? WHERE workspace_id=? AND id=?", (p["unit_cost"], ws, pid))
    conn.commit()
    return {"id": mid, "reference": ref, **{k: p[k] for k in ("qty", "raw_cost", "extra_cost", "unit_cost", "warnings")},
            "product": p["product"]["name"]}


def list_entries(conn: sqlite3.Connection, ws: str, limit: int = 200) -> list[dict[str, Any]]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT id, entry_date, product_id, product_name, qty, raw_cost, extra_cost, unit_cost, notes FROM hop_manufacture "
        "WHERE workspace_id=? AND (deleted_at IS NULL OR deleted_at='') ORDER BY entry_date DESC, id DESC LIMIT ?",
        (ws, int(limit))).fetchall()
    out = []
    for r in rows:
        lines = conn.execute("SELECT raw_name, qty, cost FROM hop_manufacture_lines WHERE workspace_id=? AND manufacture_id=?",
                             (ws, r[0])).fetchall()
        out.append({"id": r[0], "date": r[1], "product_id": r[2], "product": r[3], "qty": r[4], "raw_cost": r[5],
                    "extra_cost": r[6], "unit_cost": r[7], "notes": r[8] or "",
                    "used": [{"name": x[0], "qty": x[1], "cost": x[2]} for x in lines]})
    return out


def undo(conn: sqlite3.Connection, ws: str, mid: int) -> dict[str, Any]:
    ensure_schema(conn)
    r = conn.execute("SELECT product_id, qty, entry_date FROM hop_manufacture WHERE workspace_id=? AND id=? "
                     "AND (deleted_at IS NULL OR deleted_at='')", (ws, int(mid))).fetchone()
    if not r:
        raise ValueError("Manufacture entry not found")
    ref = f"MFG-{int(mid)}"
    date = __import__("app.hop_time", fromlist=["x"]).today_iso()
    for line in conn.execute("SELECT raw_product_id, qty FROM hop_manufacture_lines WHERE workspace_id=? AND manufacture_id=?",
                             (ws, int(mid))).fetchall():
        _journal(conn, ws, line[0], _f(line[1]), "Manufacture undone", ref, date)
    _journal(conn, ws, r[0], -_f(r[1]), "Manufacture undone", ref, date)
    conn.execute("UPDATE hop_manufacture SET deleted_at=? WHERE workspace_id=? AND id=?", (_now(), ws, int(mid)))
    conn.commit()
    return {"id": int(mid), "undone": True}


def summary_for_ai(conn: sqlite3.Connection, ws: str, q: str = "") -> dict[str, Any]:
    """Ask Nexora "kitna bana sakte hain?": each item with a raw-material list and how many can be made now."""
    ensure_schema(conn)
    ids = [r[0] for r in conn.execute("SELECT DISTINCT product_id FROM hop_bom WHERE workspace_id=?", (ws,)).fetchall()]
    items = []
    for pid in ids:
        try:
            b = get_bom(conn, ws, pid)
        except ValueError:
            continue
        name = b["product"]["name"] or ""
        if q and q.lower() not in name.lower():
            continue
        items.append({"item": name, "can_make_now": max_makeable(b["raw"]), "cost_per_piece": b["cost_per_piece"],
                      "raw": [{"name": r["name"], "per_piece": r["qty"], "in_stock": r["stock_qty"]} for r in b["raw"]]})
    return {"items": items[:40], "recent": list_entries(conn, ws, 10)}

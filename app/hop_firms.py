"""HoP multiple firms (companies) in one login.

Every HoP table is already kept apart by ``workspace_id``. A second firm is simply a second
workspace that belongs to the same login's company (``base_ws``): its own parties, items,
bills, bank, GST, settings, numbering and company profile. The phone / web send the chosen
firm in the ``X-Hop-Firm`` header; anything else falls back to the main firm.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS hop_firms (
        id INTEGER PRIMARY KEY AUTOINCREMENT, base_ws TEXT NOT NULL, firm_ws TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL, gstin TEXT, is_main INTEGER DEFAULT 0, created_at TEXT NOT NULL)""")


def _profile_name(conn, ws: str) -> str:
    try:
        r = conn.execute("SELECT firm_name FROM hop_firm_profile WHERE workspace_id=?", (ws,)).fetchone()
        return (r[0] or "").strip() if r else ""
    except sqlite3.OperationalError:
        return ""


def business_name(conn: sqlite3.Connection, ws: str, fallback: str = "") -> str:
    """The subscriber's own business name (Company profile of this firm). Never a fixed brand."""
    return _profile_name(conn, ws) or fallback


def list_firms(conn: sqlite3.Connection, base_ws: str) -> list[dict[str, Any]]:
    ensure_schema(conn)
    if not conn.execute("SELECT 1 FROM hop_firms WHERE firm_ws=?", (base_ws,)).fetchone():
        conn.execute("INSERT OR IGNORE INTO hop_firms (base_ws, firm_ws, name, is_main, created_at) VALUES (?, ?, ?, 1, ?)",
                     (base_ws, base_ws, _profile_name(conn, base_ws) or "Main firm", _now()))
        conn.commit()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, firm_ws, name, gstin, is_main FROM hop_firms WHERE base_ws=? ORDER BY is_main DESC, id", (base_ws,))]
    for r in rows:   # the company profile is the real name once it is filled in
        r["name"] = _profile_name(conn, r["firm_ws"]) or r["name"]
    return rows


def active_ws(conn: sqlite3.Connection, base_ws: str, requested: str | None) -> str:
    """The firm this request works on — only a firm of the same company, else the main one."""
    req = str(requested or "").strip()
    if not req or req == base_ws:
        return base_ws
    try:
        r = conn.execute("SELECT 1 FROM hop_firms WHERE base_ws=? AND firm_ws=?", (base_ws, req)).fetchone()
    except sqlite3.OperationalError:
        return base_ws
    return req if r else base_ws


def create(conn: sqlite3.Connection, base_ws: str, name: str, gstin: str = "", state: str = "",
           copy_settings: bool = True) -> dict[str, Any]:
    list_firms(conn, base_ws)
    name = str(name or "").strip()[:80]
    if not name:
        raise ValueError("Firm name is required")
    gstin = re.sub(r"\s", "", str(gstin or "")).upper()[:15]
    if gstin and not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", gstin):
        raise ValueError("GSTIN looks wrong")
    if conn.execute("SELECT 1 FROM hop_firms WHERE base_ws=? AND lower(name)=lower(?)", (base_ws, name)).fetchone():
        raise ValueError("A firm with this name already exists")
    n = conn.execute("SELECT COUNT(*) FROM hop_firms WHERE base_ws=?", (base_ws,)).fetchone()[0] + 1
    firm_ws = f"{base_ws}__f{n}"
    while conn.execute("SELECT 1 FROM hop_firms WHERE firm_ws=?", (firm_ws,)).fetchone():
        n += 1
        firm_ws = f"{base_ws}__f{n}"
    conn.execute("INSERT INTO hop_firms (base_ws, firm_ws, name, gstin, is_main, created_at) VALUES (?, ?, ?, ?, 0, ?)",
                 (base_ws, firm_ws, name, gstin or None, _now()))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hop_firm_profile)")}
    vals = {"workspace_id": firm_ws, "firm_name": name, "gstin": gstin or None, "state": state or None}
    keep = {k: v for k, v in vals.items() if k in cols}
    if "updated_at" in cols:
        keep["updated_at"] = _now()
    conn.execute(f"INSERT OR REPLACE INTO hop_firm_profile ({', '.join(keep)}) VALUES ({', '.join('?' * len(keep))})",
                 tuple(keep.values()))
    if copy_settings:   # same bill / GST / print settings to start with (numbering starts fresh)
        try:
            conn.execute("INSERT OR IGNORE INTO hop_app_settings (workspace_id, key, value, updated_at) "
                         "SELECT ?, key, value, updated_at FROM hop_app_settings WHERE workspace_id=? AND key NOT LIKE 'prefix_%'",
                         (firm_ws, base_ws))
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return {"firm_ws": firm_ws, "name": name}


def rename(conn: sqlite3.Connection, base_ws: str, firm_ws: str, name: str) -> None:
    name = str(name or "").strip()[:80]
    if not name:
        raise ValueError("Firm name is required")
    if not conn.execute("SELECT 1 FROM hop_firms WHERE base_ws=? AND firm_ws=?", (base_ws, firm_ws)).fetchone():
        raise ValueError("Firm not found")
    conn.execute("UPDATE hop_firms SET name=? WHERE firm_ws=?", (name, firm_ws))
    conn.execute("UPDATE hop_firm_profile SET firm_name=? WHERE workspace_id=?", (name, firm_ws))
    conn.commit()

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


UIS = ("business", "distributor")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS hop_firms (
        id INTEGER PRIMARY KEY AUTOINCREMENT, base_ws TEXT NOT NULL, firm_ws TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL, gstin TEXT, is_main INTEGER DEFAULT 0, created_at TEXT NOT NULL)""")
    # Each UI keeps its own firms (owner, 20-11: "ek ui ka single data bhi dusri ui main nahi jana chahiye").
    if "ui" not in {r[1] for r in conn.execute("PRAGMA table_info(hop_firms)")}:
        conn.execute("ALTER TABLE hop_firms ADD COLUMN ui TEXT DEFAULT 'business'")


def request_ui(explicit: str | None = None) -> str:
    """Which UI this call comes from: the ``X-Nexora-UI`` header (phone), else Business (web / old phones)."""
    ui = (explicit or "").strip().lower()
    if not ui:
        try:
            from flask import has_request_context, request

            if has_request_context():
                ui = (request.headers.get("X-Nexora-UI") or "").strip().lower()
        except Exception:
            ui = ""
    return ui if ui in UIS else "business"


def _ui_sql(ui: str) -> str:
    return "COALESCE(ui,'business')=?" if ui == "business" else "ui=?"


def main_ws(conn: sqlite3.Connection, base_ws: str, ui: str = "business") -> str:
    """The main firm of this UI. Business = the login's own workspace. Distributor = a separate workspace
    ``<base>__dist``, made the first time (empty books: own items, bills, parties, notes, settings)."""
    if ui == "business":
        return base_ws
    ensure_schema(conn)
    r = conn.execute("SELECT firm_ws FROM hop_firms WHERE base_ws=? AND ui=? ORDER BY is_main DESC, id LIMIT 1",
                     (base_ws, ui)).fetchone()
    if r:
        return r[0]
    firm_ws = f"{base_ws}__dist"
    name = "My distribution firm"
    conn.execute("INSERT OR IGNORE INTO hop_firms (base_ws, firm_ws, name, is_main, created_at, ui) VALUES (?, ?, ?, 1, ?, ?)",
                 (base_ws, firm_ws, name, _now(), ui))
    try:
        cols = {c[1] for c in conn.execute("PRAGMA table_info(hop_firm_profile)")}
        if cols and not conn.execute("SELECT 1 FROM hop_firm_profile WHERE workspace_id=?", (firm_ws,)).fetchone():
            vals = {"workspace_id": firm_ws, "firm_name": name}
            if "updated_at" in cols:
                vals["updated_at"] = _now()
            conn.execute(f"INSERT INTO hop_firm_profile ({', '.join(vals)}) VALUES ({', '.join('?' * len(vals))})",
                         tuple(vals.values()))
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return firm_ws


def _profile_name(conn, ws: str) -> str:
    try:
        r = conn.execute("SELECT firm_name FROM hop_firm_profile WHERE workspace_id=?", (ws,)).fetchone()
        return (r[0] or "").strip() if r else ""
    except sqlite3.OperationalError:
        return ""


def business_name(conn: sqlite3.Connection, ws: str, fallback: str = "") -> str:
    """The subscriber's own business name (Company profile of this firm). Never a fixed brand."""
    return _profile_name(conn, ws) or fallback


def list_firms(conn: sqlite3.Connection, base_ws: str, ui: str | None = None) -> list[dict[str, Any]]:
    """The firms of this UI only (the Business firm switcher never shows a Distributor firm, and back)."""
    ensure_schema(conn)
    ui = request_ui(ui)
    if not conn.execute("SELECT 1 FROM hop_firms WHERE firm_ws=?", (base_ws,)).fetchone():
        conn.execute("INSERT OR IGNORE INTO hop_firms (base_ws, firm_ws, name, is_main, created_at) VALUES (?, ?, ?, 1, ?)",
                     (base_ws, base_ws, _profile_name(conn, base_ws) or "Main firm", _now()))
        conn.commit()
    main_ws(conn, base_ws, ui)
    rows = [dict(r) for r in conn.execute(
        f"SELECT id, firm_ws, name, gstin, is_main, created_at FROM hop_firms WHERE base_ws=? AND {_ui_sql(ui)} "
        "ORDER BY is_main DESC, id", (base_ws, ui))]
    for r in rows:   # the company profile is the real name once it is filled in
        r["name"] = _profile_name(conn, r["firm_ws"]) or r["name"]
    return rows


def active_ws(conn: sqlite3.Connection, base_ws: str, requested: str | None, ui: str | None = None) -> str:
    """The firm this request works on — only a firm of the same company AND of the same UI, else that UI's main
    firm. So the Distributor UI can never read or write the Business books (or notes, settings…), and back."""
    ui = request_ui(ui)
    req = str(requested or "").strip()
    if ui == "business" and (not req or req == base_ws):
        return base_ws
    try:
        ensure_schema(conn)
        if req and conn.execute(f"SELECT 1 FROM hop_firms WHERE base_ws=? AND firm_ws=? AND {_ui_sql(ui)}",
                                (base_ws, req, ui)).fetchone():
            return req
        return main_ws(conn, base_ws, ui)
    except sqlite3.OperationalError:
        return base_ws if ui == "business" else f"{base_ws}__dist"


def create(conn: sqlite3.Connection, base_ws: str, name: str, gstin: str = "", state: str = "",
           copy_settings: bool = True, ui: str | None = None) -> dict[str, Any]:
    ui = request_ui(ui)
    list_firms(conn, base_ws, ui)
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
    conn.execute("INSERT INTO hop_firms (base_ws, firm_ws, name, gstin, is_main, created_at, ui) VALUES (?, ?, ?, ?, 0, ?, ?)",
                 (base_ws, firm_ws, name, gstin or None, _now(), ui))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hop_firm_profile)")}
    vals = {"workspace_id": firm_ws, "firm_name": name, "gstin": gstin or None, "state": state or None}
    keep = {k: v for k, v in vals.items() if k in cols}
    if "updated_at" in cols:
        keep["updated_at"] = _now()
    conn.execute(f"INSERT OR REPLACE INTO hop_firm_profile ({', '.join(keep)}) VALUES ({', '.join('?' * len(keep))})",
                 tuple(keep.values()))
    if copy_settings:   # same bill / GST / print settings as this UI's main firm (numbering starts fresh)
        try:
            conn.execute("INSERT OR IGNORE INTO hop_app_settings (workspace_id, key, value, updated_at) "
                         "SELECT ?, key, value, updated_at FROM hop_app_settings WHERE workspace_id=? AND key NOT LIKE 'prefix_%'",
                         (firm_ws, main_ws(conn, base_ws, ui)))
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return {"firm_ws": firm_ws, "name": name}


def move_ui(conn: sqlite3.Connection, base_ws: str, firm_ws: str, to_ui: str) -> None:
    """Put a firm under another UI (owner taps it in the firm list and confirms). Firms made before 20-11 had no UI
    and were all filed under Business, so a firm the owner made in the Distributor UI showed in the Business UI.
    The main firm of a UI never moves; nothing inside the firm is changed."""
    ensure_schema(conn)
    to_ui = str(to_ui or "").strip().lower()
    if to_ui not in UIS:
        raise ValueError("UI must be business or distributor")
    r = conn.execute("SELECT is_main, COALESCE(ui,'business') FROM hop_firms WHERE base_ws=? AND firm_ws=?",
                     (base_ws, firm_ws)).fetchone()
    if not r:
        raise ValueError("Firm not found")
    if int(r[0] or 0) or firm_ws == base_ws:
        raise ValueError("The main firm of a UI cannot be moved")
    if r[1] == to_ui:
        return
    conn.execute("UPDATE hop_firms SET ui=? WHERE base_ws=? AND firm_ws=?", (to_ui, base_ws, firm_ws))
    conn.commit()


def rename(conn: sqlite3.Connection, base_ws: str, firm_ws: str, name: str) -> None:
    name = str(name or "").strip()[:80]
    if not name:
        raise ValueError("Firm name is required")
    if not conn.execute("SELECT 1 FROM hop_firms WHERE base_ws=? AND firm_ws=?", (base_ws, firm_ws)).fetchone():
        raise ValueError("Firm not found")
    conn.execute("UPDATE hop_firms SET name=? WHERE firm_ws=?", (name, firm_ws))
    conn.execute("UPDATE hop_firm_profile SET firm_name=? WHERE workspace_id=?", (name, firm_ws))
    conn.commit()

"""Business notes — anything the owner wants to remember (party talks, ideas, to-check), typed or via Ask Nexora.

* A note can be tied to a party (customer / supplier) and have a "remind on" date.
* Ask Nexora drafts a note with the add_business_note action; it is saved only after the user confirms.
* Deleting moves it out of the list (deleted_at); nothing is hard-deleted.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _s(v: Any) -> str:
    return str(v or "").strip()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_notes (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               workspace_id TEXT NOT NULL,
               title TEXT,
               body TEXT NOT NULL,
               party_type TEXT,
               party_id INTEGER,
               party_name TEXT,
               remind_on TEXT,
               pinned INTEGER NOT NULL DEFAULT 0,
               source TEXT,
               created_by TEXT,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               deleted_at TEXT
           )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_hop_notes_ws ON hop_notes(workspace_id, deleted_at)")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hop_notes)")}
    if "comments_json" not in cols:   # replies in the note: [{by, text, kind, at}]
        conn.execute("ALTER TABLE hop_notes ADD COLUMN comments_json TEXT")


def _party(conn: sqlite3.Connection, ws: str, payload: dict) -> tuple[str | None, int | None, str | None]:
    """Party from id, or matched by name (customers first, then suppliers)."""
    pt, pid = _s(payload.get("party_type")) or None, payload.get("party_id")
    if pid:
        table = "hop_vendors" if pt == "vendor" else "hop_customers"
        r = conn.execute(f"SELECT id, company FROM {table} WHERE workspace_id=? AND id=?", (ws, int(pid))).fetchone()
        if r:
            return ("vendor" if pt == "vendor" else "customer"), int(r[0]), r[1]
    name = _s(payload.get("party_name"))
    if not name:
        return None, None, None
    for kind, table in (("customer", "hop_customers"), ("vendor", "hop_vendors")):
        r = conn.execute(f"SELECT id, company FROM {table} WHERE workspace_id=? AND lower(company) LIKE ? ORDER BY length(company) LIMIT 1",
                         (ws, f"%{name.lower()}%")).fetchone()
        if r:
            return kind, int(r[0]), r[1]
    return None, None, name   # keep the name even if it is not a party in the books


def create(conn: sqlite3.Connection, ws: str, payload: dict, *, user: str = "") -> dict[str, Any]:
    ensure_schema(conn)
    body = _s(payload.get("text") or payload.get("body"))
    if not body:
        raise ValueError("Write the note")
    pt, pid, pname = _party(conn, ws, payload)
    now = _now()
    cur = conn.execute(
        """INSERT INTO hop_notes (workspace_id, title, body, party_type, party_id, party_name, remind_on, pinned, source,
                                  created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ws, _s(payload.get("title"))[:120] or None, body[:4000], pt, pid, pname, _s(payload.get("remind_on"))[:10] or None,
         1 if payload.get("pinned") in (True, 1, "1", "true") else 0, _s(payload.get("source"))[:20] or "manual", user or None,
         now, now))
    conn.commit()
    return get(conn, ws, int(cur.lastrowid))


def _with_comments(d: dict[str, Any]) -> dict[str, Any]:
    import json as _json

    try:
        d["comments"] = _json.loads(d.get("comments_json") or "[]")
    except Exception:
        d["comments"] = []
    d.pop("comments_json", None)
    return d


def get(conn: sqlite3.Connection, ws: str, note_id: int) -> dict[str, Any]:
    ensure_schema(conn)
    r = conn.execute("SELECT * FROM hop_notes WHERE workspace_id=? AND id=? AND deleted_at IS NULL", (ws, int(note_id))).fetchone()
    if not r:
        raise LookupError("Note not found")
    return _with_comments(dict(r))


def add_comment(conn: sqlite3.Connection, ws: str, note_id: int, payload: dict) -> dict[str, Any]:
    """Add a reply to a note: what was done, or a plan (kind='plan')."""
    import json as _json

    text = _s(payload.get("text"))
    if not text:
        raise ValueError("Write the comment")
    note = get(conn, ws, note_id)
    items = note.get("comments") or []
    items.append({"by": _s(payload.get("by"))[:40] or "User", "kind": "plan" if payload.get("kind") == "plan" else "comment",
                  "text": text[:8000], "at": _now()})
    conn.execute("UPDATE hop_notes SET comments_json=?, updated_at=? WHERE workspace_id=? AND id=?",
                 (_json.dumps(items, ensure_ascii=False), _now(), ws, int(note_id)))
    conn.commit()
    return get(conn, ws, note_id)


def update(conn: sqlite3.Connection, ws: str, note_id: int, payload: dict) -> dict[str, Any]:
    get(conn, ws, note_id)
    sets, vals = [], []
    if "text" in payload or "body" in payload:
        body = _s(payload.get("text") or payload.get("body"))
        if not body:
            raise ValueError("Write the note")
        sets.append("body=?"); vals.append(body[:4000])
    for k, n in (("title", 120), ("remind_on", 10)):
        if k in payload:
            sets.append(f"{k}=?"); vals.append(_s(payload.get(k))[:n] or None)
    if "pinned" in payload:
        sets.append("pinned=?"); vals.append(1 if payload.get("pinned") in (True, 1, "1", "true") else 0)
    if "party_name" in payload or "party_id" in payload:
        pt, pid, pname = _party(conn, ws, payload)
        sets += ["party_type=?", "party_id=?", "party_name=?"]; vals += [pt, pid, pname]
    if sets:
        conn.execute(f"UPDATE hop_notes SET {', '.join(sets)}, updated_at=? WHERE workspace_id=? AND id=?",
                     [*vals, _now(), ws, int(note_id)])
        conn.commit()
    return get(conn, ws, note_id)


def delete(conn: sqlite3.Connection, ws: str, note_id: int) -> None:
    get(conn, ws, note_id)
    conn.execute("UPDATE hop_notes SET deleted_at=? WHERE workspace_id=? AND id=?", (_now(), ws, int(note_id)))
    conn.commit()


def search(conn: sqlite3.Connection, ws: str, q: str = "", party_name: str = "", limit: int = 100) -> list[dict[str, Any]]:
    ensure_schema(conn)
    sql, args = "SELECT * FROM hop_notes WHERE workspace_id=? AND deleted_at IS NULL", [ws]
    if q:
        like = f"%{q.strip().lower()}%"
        sql += " AND (lower(body) LIKE ? OR lower(COALESCE(title,'')) LIKE ? OR lower(COALESCE(party_name,'')) LIKE ?)"
        args += [like, like, like]
    if party_name:
        sql += " AND lower(COALESCE(party_name,'')) LIKE ?"
        args.append(f"%{party_name.strip().lower()}%")
    sql += " ORDER BY pinned DESC, datetime(updated_at) DESC LIMIT ?"
    args.append(int(limit))
    return [_with_comments(dict(r)) for r in conn.execute(sql, args).fetchall()]

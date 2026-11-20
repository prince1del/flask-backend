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
    if "done_at" not in cols:   # marked done (the work in the note is finished)
        conn.execute("ALTER TABLE hop_notes ADD COLUMN done_at TEXT")
        conn.execute("ALTER TABLE hop_notes ADD COLUMN done_by TEXT")
    if "photos_json" not in cols:   # photos / screenshots kept with the note: [{id, at}]
        conn.execute("ALTER TABLE hop_notes ADD COLUMN photos_json TEXT")
    if "checked_at" not in cols:   # founder's ✓✓ on a note for Claude: checked, the work is fine (owner 21-11)
        conn.execute("ALTER TABLE hop_notes ADD COLUMN checked_at TEXT")
        conn.execute("ALTER TABLE hop_notes ADD COLUMN checked_by TEXT")


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
    try:
        d["photos"] = _json.loads(d.get("photos_json") or "[]")
    except Exception:
        d["photos"] = []
    d.pop("photos_json", None)
    d["done"] = bool(d.get("done_at"))
    # who the work is for (owner 21-11: "kiske liye kar raha hai kaise pata chalega")
    d["for_whom"] = "Claude" if (_s(d.get("party_name")).lower() == "claude"
                                 or _s(d.get("body")).startswith("[For Claude]")) else None
    d["checked"] = bool(d.get("checked_at"))
    d["raised_again"] = sum(1 for c in d["comments"] if isinstance(c, dict) and c.get("kind") == "status"
                            and _s(c.get("text")).startswith("↩"))
    return d


def _link(payload: dict) -> dict | None:
    """Where the fix can be seen: {view: a screen key of the app (e.g. dist_brand_desk), label: "Distributor → Brand desk"}.
    Owner 21-11: with "done", a link so the founder can open the screen and check the query is solved."""
    import re

    v = payload.get("link")
    view = _s(v.get("view") if isinstance(v, dict) else v)
    if not view:
        return None
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", view):
        raise ValueError("Link must be a screen key like dist_brand_desk")
    label = _s((v.get("label") if isinstance(v, dict) else None) or payload.get("link_label"))[:80]
    return {"view": view, "label": label or view}


def is_for_claude(payload: dict) -> bool:
    """A note written for Claude (party "claude" or text starting "[For Claude]") — only the founder account may."""
    return _s(payload.get("party_name")).lower() == "claude" or         _s(payload.get("text") or payload.get("body")).startswith("[For Claude]")


def _status_line(items: list, by: str, text: str, link: dict | None = None) -> list:
    """A history line in the note: who marked it done / raised it again, and when (+ where to check it)."""
    item = {"by": by[:40] or "User", "kind": "status", "text": text[:2000], "at": _now()}
    if link:
        item["link"] = link
    items.append(item)
    return items


# Note number in its firm (#1, #2 … in the order notes were made; deleted notes keep theirs, so a number never moves).
# Owner 21-11: "notes main numbering do taaki pata chale konsa note hai".
_NO = "(SELECT COUNT(*) FROM hop_notes x WHERE x.workspace_id=hop_notes.workspace_id AND x.id<=hop_notes.id) AS no"


def get(conn: sqlite3.Connection, ws: str, note_id: int) -> dict[str, Any]:
    ensure_schema(conn)
    r = conn.execute(f"SELECT *, {_NO} FROM hop_notes WHERE workspace_id=? AND id=? AND deleted_at IS NULL",
                     (ws, int(note_id))).fetchone()
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
    by = _s(payload.get("by"))[:40] or "User"
    item = {"by": by, "kind": "plan" if payload.get("kind") == "plan" else "comment", "text": text[:8000], "at": _now()}
    link = _link(payload)
    if link:
        item["link"] = link
    items.append(item)
    # The user writes again in a finished note = the work is not right yet: the note opens again
    # (owner 21-11: "user ko kaam pasand nahi aaya to query dubara raise kar sakta hai, job done ka sign hat jayega").
    reopen = note.get("done") and by.lower() != "claude"
    if reopen:
        _status_line(items, by, "↩ Raised again")
        conn.execute("UPDATE hop_notes SET comments_json=?, done_at=NULL, done_by=NULL, checked_at=NULL, checked_by=NULL, updated_at=? "
                     "WHERE workspace_id=? AND id=?", (_json.dumps(items, ensure_ascii=False), _now(), ws, int(note_id)))
    else:
        conn.execute("UPDATE hop_notes SET comments_json=?, updated_at=? WHERE workspace_id=? AND id=?",
                     (_json.dumps(items, ensure_ascii=False), _now(), ws, int(note_id)))
    conn.commit()
    return get(conn, ws, note_id)


def update(conn: sqlite3.Connection, ws: str, note_id: int, payload: dict) -> dict[str, Any]:
    import json as _json

    note = get(conn, ws, note_id)
    sets, vals = [], []
    if "text" in payload or "body" in payload:
        body = _s(payload.get("text") or payload.get("body"))
        if not body:
            raise ValueError("Write the note")
        sets.append("body=?"); vals.append(body[:4000])
    for k, n in (("title", 120), ("remind_on", 10)):
        if k in payload:
            sets.append(f"{k}=?"); vals.append(_s(payload.get(k))[:n] or None)
    if payload.get("checked") in (True, 1, "1", "true"):   # founder's ✓✓ — only a done note for Claude
        if not note.get("for_whom"):
            raise ValueError("✓✓ is only for notes for Claude")
        if not note.get("done") and payload.get("done") not in (True, 1, "1", "true"):
            raise ValueError("Mark the note done first, then ✓✓")
        sets += ["checked_at=?", "checked_by=?"]
        vals += [_now(), _s(payload.get("checked_by"))[:40] or "Founder"]
        items = list(note.get("comments") or [])
        if not any(x.startswith("comments_json") for x in sets):
            sets.append("comments_json=?")
            vals.append(_json.dumps(_status_line(items, _s(payload.get("checked_by"))[:40] or "Founder", "✓✓ Checked — work is fine"),
                                    ensure_ascii=False))
    if "done" in payload:   # mark done (Claude or the user) / raise again (the work is not right yet)
        done = payload.get("done") in (True, 1, "1", "true")
        by = _s(payload.get("done_by"))[:40] or "User"
        sets += ["done_at=?", "done_by=?"]
        vals += [_now() if done else None, by if done else None]
        if not done:
            sets += ["checked_at=?", "checked_by=?"]; vals += [None, None]
        if done != bool(note.get("done")):
            reason = _s(payload.get("reason"))
            line = "✓ Marked done" if done else ("↩ Raised again" + (f": {reason}" if reason else ""))
            sets.append("comments_json=?")
            vals.append(_json.dumps(_status_line(list(note.get("comments") or []), by, line,
                                                 _link(payload) if done else None), ensure_ascii=False))
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
    n = get(conn, ws, note_id)
    if n.get("for_whom") and not n.get("checked"):
        raise ValueError("A note for Claude can be deleted after the founder's ✓✓ (checked, the work is fine).")
    conn.execute("UPDATE hop_notes SET deleted_at=? WHERE workspace_id=? AND id=?", (_now(), ws, int(note_id)))
    conn.commit()


def bulk_delete(conn: sqlite3.Connection, ws: str, note_ids: list[int]) -> dict[str, Any]:
    """Soft-delete many notes at once (select → Delete N on the phone)."""
    ensure_schema(conn)
    ids = sorted({int(i) for i in (note_ids or []) if str(i).isdigit() or isinstance(i, int)})
    if not ids:
        raise ValueError("Pick at least one note")
    now = _now()
    deleted = 0
    skipped = 0
    for nid in ids:
        try:
            n = get(conn, ws, nid)
        except LookupError:
            continue
        if n.get("for_whom") and not n.get("checked"):
            skipped += 1   # a note for Claude waits for the founder's ✓✓
            continue
        cur = conn.execute(
            "UPDATE hop_notes SET deleted_at=? WHERE workspace_id=? AND id=? AND deleted_at IS NULL",
            (now, ws, nid),
        )
        deleted += cur.rowcount or 0
    conn.commit()
    return {"deleted": deleted, "requested": len(ids), "skipped_not_checked": skipped}


def search(conn: sqlite3.Connection, ws: str, q: str = "", party_name: str = "", limit: int = 100) -> list[dict[str, Any]]:
    ensure_schema(conn)
    import re

    sql, args = f"SELECT * FROM (SELECT *, {_NO} FROM hop_notes WHERE workspace_id=?) WHERE deleted_at IS NULL", [ws]
    m = re.fullmatch(r"#?\s*(\d{1,6})", (q or "").strip())
    if m:   # "#12" or "12" → that note (and notes that have the number in their text)
        like = f"%{m.group(1)}%"
        sql += " AND (no=? OR lower(body) LIKE ? OR lower(COALESCE(title,'')) LIKE ?)"
        args += [int(m.group(1)), like, like]
        q = ""
    if q:
        like = f"%{q.strip().lower()}%"
        sql += " AND (lower(body) LIKE ? OR lower(COALESCE(title,'')) LIKE ? OR lower(COALESCE(party_name,'')) LIKE ?)"
        args += [like, like, like]
    if party_name:
        sql += " AND lower(COALESCE(party_name,'')) LIKE ?"
        args.append(f"%{party_name.strip().lower()}%")
    sql += " ORDER BY (done_at IS NOT NULL), pinned DESC, datetime(updated_at) DESC LIMIT ?"
    args.append(int(limit))
    return [_with_comments(dict(r)) for r in conn.execute(sql, args).fetchall()]


# -- photos / screenshots on a note (owner: "notes main screenshot ya photo add karne ka option do") --
MAX_PHOTOS = 40


def _photo_dir(db_path: str, ws: str) -> str:
    import os
    import re

    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(ws))[:80] or "main"
    d = os.path.join(os.path.dirname(os.path.abspath(db_path)), "note_photos", safe)
    os.makedirs(d, exist_ok=True)
    return d


def photo_path(db_path: str, ws: str, note_id: int, photo_id: str) -> str:
    import os
    import re

    pid = re.sub(r"[^0-9a-z]", "", str(photo_id).lower())[:32]
    if not pid:
        raise LookupError("Photo not found")
    return os.path.join(_photo_dir(db_path, ws), f"{int(note_id)}_{pid}.jpg")


def _set_photos(conn: sqlite3.Connection, ws: str, note_id: int, items: list) -> None:
    import json as _json

    conn.execute("UPDATE hop_notes SET photos_json=?, updated_at=? WHERE workspace_id=? AND id=?",
                 (_json.dumps(items), _now(), ws, int(note_id)))
    conn.commit()


def add_photo(conn: sqlite3.Connection, db_path: str, ws: str, note_id: int, data: bytes) -> dict[str, Any]:
    import uuid

    from app.hop_product_photos import to_jpeg

    note = get(conn, ws, note_id)
    items = note.get("photos") or []
    if len(items) >= MAX_PHOTOS:
        raise ValueError(f"A note can keep up to {MAX_PHOTOS} photos")
    jpg = to_jpeg(data, 1600)   # screenshots stay readable
    pid = uuid.uuid4().hex[:12]
    with open(photo_path(db_path, ws, note_id, pid), "wb") as f:
        f.write(jpg)
    items.append({"id": pid, "at": _now()})
    _set_photos(conn, ws, note_id, items)
    return get(conn, ws, note_id)


def remove_photo(conn: sqlite3.Connection, db_path: str, ws: str, note_id: int, photo_id: str) -> dict[str, Any]:
    import os

    note = get(conn, ws, note_id)
    items = [p for p in (note.get("photos") or []) if p.get("id") != photo_id]
    p = photo_path(db_path, ws, note_id, photo_id)
    if os.path.exists(p):
        os.remove(p)
    _set_photos(conn, ws, note_id, items)
    return get(conn, ws, note_id)

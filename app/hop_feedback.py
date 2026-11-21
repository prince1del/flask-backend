"""Feedback from subscribers to NEXORA Customer care (owner 21-11: "user ko feedback dena hai woh kaise dega ... puri
wiring banana"; users always see "Customer care" — never a person's name or an email).

User (any business, any UI) → ⓘ guide → Report a problem / Suggest a change → text + the screen's screenshot, screen,
UI, firm, app version → saved here (one inbox for the whole platform, never inside a business's own books) →
Customer care (the platform owner's login) gets an email at once + "Customer care · n new" in the menu + the
Customer care inbox (Help & feedback) → sets Open / Working / Done and writes a reply → the user sees
"1 reply from Customer care" in Help & feedback.
Screenshots: <db folder>/feedback_shots/<id>.jpg (in the server backups of /var/data).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

KINDS = ("problem", "idea")
STATUSES = ("open", "working", "done")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _s(v: Any, n: int = 200) -> str:
    return str(v or "").strip()[:n]


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS nexora_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL, workspace_id TEXT, ui TEXT, firm_name TEXT,
        user_id INTEGER, username TEXT, kind TEXT NOT NULL, text TEXT NOT NULL,
        screen TEXT, app_version TEXT, has_shot INTEGER DEFAULT 0,
        status TEXT DEFAULT 'open', reply TEXT, replied_at TEXT,
        founder_seen INTEGER DEFAULT 0, user_seen INTEGER DEFAULT 1)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nexora_feedback_user ON nexora_feedback(user_id)")


def shot_path(db_path: str, fid: int) -> str:
    d = os.path.join(os.path.dirname(os.path.abspath(db_path)), "feedback_shots")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{int(fid)}.jpg")


def founder_email() -> str:
    from app.services.subscriptions import founder_username

    explicit = (os.getenv("NEXORA_FEEDBACK_EMAIL") or "").strip()
    if explicit:
        return explicit
    u = founder_username()
    return u if "@" in u else ""


def is_founder(user: dict[str, Any] | None) -> bool:
    from app.services.subscriptions import founder_username

    return isinstance(user, dict) and str(user.get("username") or "").strip().lower() == founder_username().strip().lower()


def _mail_founder(row: dict[str, Any], shot: bytes | None) -> None:
    """Email the founder in the background (a slow mail server never holds up the user)."""
    to = founder_email()
    if not to:
        return

    def go():
        try:
            from app.services import outbound_mail

            kind = "Problem" if row["kind"] == "problem" else "Idea"
            body = (f"NEXORA Customer care — new {kind.lower()}\n"
                    f"From {row.get('username') or 'a user'} — {row.get('firm_name') or row.get('workspace_id')}\n"
                    f"UI: {row.get('ui') or '-'} · Screen: {row.get('screen') or '-'} · App: {row.get('app_version') or '-'}\n\n"
                    f"{row['text']}\n\nReply in the app: Help & feedback → Customer care inbox (#{row['id']}).")
            att = [(f"feedback-{row['id']}.jpg", shot, "image/jpeg")] if shot else None
            outbound_mail.send_email(to=to, subject=f"NEXORA Customer care #{row['id']} · {kind} · {row.get('firm_name') or ''}".strip(),
                                     body=body, attachments=att)
        except Exception:
            pass

    threading.Thread(target=go, daemon=True).start()


def create(conn: sqlite3.Connection, db_path: str, *, user: dict[str, Any], ws: str, ui: str, firm_name: str,
           kind: str, text: str, screen: str = "", app_version: str = "", shot: bytes | None = None,
           mail: bool = True) -> dict[str, Any]:
    ensure_schema(conn)
    kind = _s(kind, 20).lower() or "problem"
    if kind not in KINDS:
        raise ValueError("kind must be problem or idea")
    text = _s(text, 4000)
    if len(text) < 3:
        raise ValueError("Write a few words about the problem or the idea")
    jpg = None
    if shot:
        from app.hop_product_photos import to_jpeg

        try:
            jpg = to_jpeg(shot, 1600)
        except ValueError:
            jpg = None
    cur = conn.execute(
        "INSERT INTO nexora_feedback (created_at, workspace_id, ui, firm_name, user_id, username, kind, text, screen, "
        "app_version, has_shot) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (_now(), _s(ws, 80), _s(ui, 20), _s(firm_name, 120), int(user.get("user_id") or 0) or None,
         _s(user.get("username") or user.get("email"), 120), kind, text, _s(screen, 60), _s(app_version, 30), 1 if jpg else 0))
    fid = int(cur.lastrowid)
    if jpg:
        with open(shot_path(db_path, fid), "wb") as f:
            f.write(jpg)
    conn.commit()
    row = get(conn, fid)
    if mail:
        _mail_founder(row, jpg)
    return row


def get(conn: sqlite3.Connection, fid: int) -> dict[str, Any] | None:
    ensure_schema(conn)
    r = conn.execute("SELECT * FROM nexora_feedback WHERE id=?", (int(fid),)).fetchone()
    return dict(r) if r else None


def mine(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    """The user's own feedback, newest first; opening the list marks replies as seen."""
    ensure_schema(conn)
    rows = [dict(r) for r in conn.execute("SELECT * FROM nexora_feedback WHERE user_id=? ORDER BY id DESC LIMIT 200",
                                          (int(user_id),))]
    conn.execute("UPDATE nexora_feedback SET user_seen=1 WHERE user_id=? AND user_seen=0", (int(user_id),))
    conn.commit()
    return rows


def inbox(conn: sqlite3.Connection, status: str = "") -> list[dict[str, Any]]:
    """Founder: every subscriber's feedback (open first), newest first; marks them seen."""
    ensure_schema(conn)
    sql, args = "SELECT * FROM nexora_feedback", []
    if status in STATUSES:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'working' THEN 1 ELSE 2 END, id DESC LIMIT 500"
    rows = [dict(r) for r in conn.execute(sql, args)]
    conn.execute("UPDATE nexora_feedback SET founder_seen=1 WHERE founder_seen=0")
    conn.commit()
    return rows


def reply(conn: sqlite3.Connection, fid: int, *, status: str = "", text: str = "") -> dict[str, Any]:
    ensure_schema(conn)
    row = get(conn, fid)
    if not row:
        raise LookupError("Feedback not found")
    status = _s(status, 20).lower() or row["status"]
    if status not in STATUSES:
        raise ValueError("status must be open, working or done")
    text = _s(text, 4000)
    if text:
        conn.execute("UPDATE nexora_feedback SET status=?, reply=?, replied_at=?, user_seen=0 WHERE id=?",
                     (status, text, _now(), int(fid)))
    else:
        conn.execute("UPDATE nexora_feedback SET status=?, user_seen=CASE WHEN status!=? THEN 0 ELSE user_seen END WHERE id=?",
                     (status, status, int(fid)))
    conn.commit()
    return get(conn, fid)


def counts(conn: sqlite3.Connection, user: dict[str, Any]) -> dict[str, Any]:
    """Menu badge: founder → new feedback; everyone → replies not read yet."""
    ensure_schema(conn)
    uid = int(user.get("user_id") or 0)
    out = {"is_founder": is_founder(user),
           "replies": conn.execute("SELECT COUNT(*) FROM nexora_feedback WHERE user_id=? AND user_seen=0", (uid,)).fetchone()[0]}
    if out["is_founder"]:
        out["new"] = conn.execute("SELECT COUNT(*) FROM nexora_feedback WHERE founder_seen=0").fetchone()[0]
        out["open"] = conn.execute("SELECT COUNT(*) FROM nexora_feedback WHERE status!='done'").fetchone()[0]
    return out

"""Owner approves every sign-in of an invited team member, for one phone.

* A member (a login with a ``member_access`` row — i.e. joined by invite) who
  signs in or signs up gets a session that is **pending** until the company
  owner/admin approves it. While pending, every API call answers 403
  LOGIN_PENDING except sign-out and the approval check.
* An approved session lasts until the member signs out (the refresh token slides
  forward on use). It is tied to that session id / phone: signing in on another
  phone makes a new request and the old phone is signed out (one live device).
* Signing out ends the approval; the next sign-in needs a new one.
* Owners, the super admin and logins from before this feature (no
  ``member_access`` row) never need approval, so nobody can lock the owner out.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any

_ALLOWED_WHILE_PENDING = ("/api/v1/auth/",)
_CACHE_TTL = 15
_cache: dict[str, tuple[float, str | None]] = {}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS login_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            workspace_id TEXT NOT NULL,
            device_label TEXT,
            ip TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            requested_at TEXT NOT NULL,
            decided_by INTEGER,
            decided_at TEXT,
            ended_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_login_appr_ws ON login_approvals(workspace_id, status)")
    conn.commit()


def _connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _invalidate(session_id: str | None = None) -> None:
    with _lock:
        if session_id is None:
            _cache.clear()
        else:
            _cache.pop(session_id, None)


def needs_approval(db_path, user_id: int, is_owner_flag: bool = False) -> bool:
    """Invited members only (they have a member_access row); never the company/workspace owner."""
    if is_owner_flag:
        return False
    with _connect(db_path) as conn:
        try:
            row = conn.execute("SELECT 1 FROM member_access WHERE user_id = ?", (int(user_id),)).fetchone()
        except sqlite3.OperationalError:
            return False
        if row is None:
            return False
        try:
            owner = conn.execute(
                """SELECT c.owner_user_id FROM users u JOIN workspace_registry w ON w.workspace_id = u.workspace_id
                   JOIN companies c ON c.id = w.company_id WHERE u.id = ?""", (int(user_id),)).fetchone()
        except sqlite3.OperationalError:
            owner = None
    return not (owner and owner[0] is not None and int(owner[0]) == int(user_id))


def request_login(db_path, user_id: int, workspace_id: str, session_id: str,
                  device_label: str | None, ip: str | None) -> None:
    """New sign-in: earlier sessions of this login stop; this one waits for the owner."""
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE login_approvals SET status = 'ended', ended_at = ? WHERE user_id = ? AND status IN ('pending', 'approved')",
            (now, int(user_id)),
        )
        conn.execute(
            """INSERT INTO login_approvals (session_id, user_id, workspace_id, device_label, ip, status, requested_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (session_id, int(user_id), workspace_id, (device_label or "")[:120] or None, (ip or "")[:60] or None, now),
        )
        conn.commit()
    _invalidate()


def session_state(db_path, session_id: str | None) -> str | None:
    """'pending' / 'approved' / 'denied' / 'ended', or None when this session needs no approval."""
    if not session_id:
        return None
    now = time.monotonic()
    with _lock:
        hit = _cache.get(session_id)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    with _connect(db_path) as conn:
        r = conn.execute("SELECT status FROM login_approvals WHERE session_id = ?", (session_id,)).fetchone()
    value = r["status"] if r else None
    with _lock:
        _cache[session_id] = (now, value)
    return value


def denied(db_path, user: dict[str, Any] | None, path: str) -> tuple[str, str] | None:
    if not isinstance(user, dict) or path.startswith(_ALLOWED_WHILE_PENDING):
        return None
    state = session_state(db_path, user.get("sid"))
    if state is None or state == "approved":
        return None
    if state == "pending":
        return ("LOGIN_PENDING", "Waiting for your company owner to approve this sign-in.")
    if state == "denied":
        return ("LOGIN_DENIED", "Your company owner did not allow this sign-in.")
    return ("SESSION_REVOKED", "Signed out. Sign in again (your company owner will approve it).")


def end_session(db_path, session_id: str | None) -> None:
    if not session_id:
        return
    with _connect(db_path) as conn:
        conn.execute("UPDATE login_approvals SET status = 'ended', ended_at = ? WHERE session_id = ? AND status <> 'ended'",
                     (_now(), session_id))
        conn.commit()
    _invalidate(session_id)


def my_state(db_path, session_id: str | None) -> dict[str, Any]:
    state = session_state(db_path, session_id)
    return {"approval": state or "not_needed"}


# ── Owner / admin ────────────────────────────────────────────────────────────

def _manager_ws(db, manager: dict[str, Any]) -> str:
    from app.services import company_invites as ci

    if not ci.can_manage_invites(db, manager):
        raise PermissionError("Only your company's owner or admin can approve sign-ins")
    ws = str(manager.get("workspace_id") or "")
    if not ws or ws == "default":
        raise PermissionError("Your login has no company workspace")
    return ws


def pending_requests(db, manager: dict[str, Any]) -> list[dict[str, Any]]:
    ws = _manager_ws(db, manager)
    with _connect(db.db_path) as conn:
        rows = conn.execute(
            """SELECT a.id, a.user_id, a.device_label, a.ip, a.requested_at, u.username, u.full_name, u.role,
                      u.email, u.phone
               FROM login_approvals a JOIN users u ON u.id = a.user_id
               WHERE a.workspace_id = ? AND u.workspace_id = ? AND a.status = 'pending'
               ORDER BY a.id""",
            (ws, ws),
        ).fetchall()
    return [dict(r) for r in rows]


def decide(db, manager: dict[str, Any], request_id: int, approve: bool) -> dict[str, Any]:
    ws = _manager_ws(db, manager)
    with _connect(db.db_path) as conn:
        r = conn.execute(
            """SELECT a.* FROM login_approvals a JOIN users u ON u.id = a.user_id
               WHERE a.id = ? AND a.workspace_id = ? AND u.workspace_id = ? AND a.status = 'pending'""",
            (int(request_id), ws, ws),
        ).fetchone()
        if r is None:
            raise LookupError("Sign-in request not found (it may be old or already decided)")
        if int(r["user_id"]) == int(manager["user_id"]):
            raise PermissionError("You cannot approve your own sign-in")
        conn.execute("UPDATE login_approvals SET status = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                     ("approved" if approve else "denied", int(manager["user_id"]), _now(), int(request_id)))
        conn.commit()
    _invalidate(r["session_id"])
    return {"id": int(request_id), "status": "approved" if approve else "denied"}


def device_of(db_path, workspace_id: str) -> dict[int, dict[str, Any]]:
    """Signed-in phone per member (for the Team dashboard)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT user_id, device_label, decided_at, status FROM login_approvals
               WHERE workspace_id = ? AND status IN ('approved', 'pending') ORDER BY id""",
            (workspace_id,),
        ).fetchall()
    return {r["user_id"]: {"device": r["device_label"], "since": r["decided_at"], "status": r["status"]} for r in rows}


def sign_out_member(db, manager: dict[str, Any], member_id: int) -> None:
    """Owner signs a member's phone out; their next sign-in needs approval again."""
    ws = _manager_ws(db, manager)
    with _connect(db.db_path) as conn:
        u = conn.execute("SELECT workspace_id FROM users WHERE id = ?", (int(member_id),)).fetchone()
        if u is None or u["workspace_id"] != ws:
            raise LookupError("Member not found in your company")
        conn.execute("UPDATE login_approvals SET status = 'ended', ended_at = ? WHERE user_id = ? AND status IN ('pending', 'approved')",
                     (_now(), int(member_id)))
        conn.commit()
    _invalidate()

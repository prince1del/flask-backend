"""Per-login subscription: 7-day trial, then activation codes from the super admin.

Each login has ``trial_ends_at`` and ``paid_until`` on the users table. Both
NULL means the login is unlimited (every account that existed before this
feature, and logins an admin creates by hand). Self sign-ups get a trial.

The super admin (WORKSPACE_OWNER_USERNAME / is_workspace_owner) generates
single-use codes worth N days. A user types the code into their app and their
own access is extended by N days from today, or from their current end date
if it is still in the future.

Once a login's access has ended, every authenticated API call except the
allow-list below answers 402 SUBSCRIPTION_EXPIRED until a code is redeemed.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

TRIAL_DAYS = 7
MAX_CODE_DAYS = 3660
MAX_BATCH = 50

_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 12

# Paths an expired login can still reach: sign in/out, see its plan, redeem a
# code, and pull app updates.
_ALLOWED_PREFIXES = (
    "/api/v1/subscription",
    "/api/v1/auth",
    "/api/v1/app",
    "/api/v1/signup",
    "/login",
    "/logout",
)

_CACHE_TTL_SECONDS = 60
_cache: dict[int, tuple[float, str | None]] = {}
_cache_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize_code(code: str | None) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def display_code(code: str) -> str:
    c = normalize_code(code)
    return "-".join(c[i:i + 4] for i in range(0, len(c), 4))


def founder_username() -> str:
    return (os.getenv("WORKSPACE_OWNER_USERNAME") or "kunwar1del").strip() or "kunwar1del"


def is_super_admin(user: dict[str, Any] | None) -> bool:
    if not isinstance(user, dict):
        return False
    if user.get("is_workspace_owner"):
        return True
    return str(user.get("username") or "").strip().lower() == founder_username().lower()


def ensure_schema(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if cols and "trial_ends_at" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN trial_ends_at TEXT")
    if cols and "paid_until" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN paid_until TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subscription_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            duration_days INTEGER NOT NULL,
            for_username TEXT,
            note TEXT,
            created_by_user_id INTEGER,
            created_at TEXT NOT NULL,
            redeemed_by_user_id INTEGER,
            redeemed_by_username TEXT,
            redeemed_at TEXT,
            revoked_at TEXT
        )
        """
    )
    conn.commit()


def _connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _invalidate(user_id: int) -> None:
    with _cache_lock:
        _cache.pop(int(user_id), None)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def access_ends_at(trial_ends_at: str | None, paid_until: str | None) -> datetime | None:
    """Latest of trial end and paid end; None means unlimited."""
    ends = [d for d in (_parse(trial_ends_at), _parse(paid_until)) if d is not None]
    return max(ends) if ends else None


def start_trial(db_path, user_id: int, days: int = TRIAL_DAYS) -> str:
    ends = (_now() + timedelta(days=days)).isoformat()
    with _connect(db_path) as conn:
        conn.execute("UPDATE users SET trial_ends_at = ? WHERE id = ?", (ends, int(user_id)))
        conn.commit()
    _invalidate(user_id)
    return ends


def status_for_user(db_path, user_id: int) -> dict[str, Any]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, username, trial_ends_at, paid_until FROM users WHERE id = ?",
            (int(user_id),),
        ).fetchone()
    if row is None:
        return {"status": "unknown"}
    return _status_from_row(row)


def _status_from_row(row) -> dict[str, Any]:
    ends = access_ends_at(row["trial_ends_at"], row["paid_until"])
    if ends is None:
        return {"status": "unlimited", "ends_at": None, "days_left": None}
    now = _now()
    paid = _parse(row["paid_until"])
    on_paid = paid is not None and paid >= ends
    if ends <= now:
        status = "expired"
    else:
        status = "active" if on_paid else "trial"
    seconds_left = (ends - now).total_seconds()
    days_left = max(0, int((seconds_left + 86399) // 86400))
    return {"status": status, "ends_at": ends.isoformat(), "days_left": days_left}


def _cached_ends_at(db_path, user_id: int) -> str | None:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(int(user_id))
        if hit and now - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT trial_ends_at, paid_until FROM users WHERE id = ?", (int(user_id),)
        ).fetchone()
    ends = access_ends_at(row["trial_ends_at"], row["paid_until"]) if row else None
    value = ends.isoformat() if ends else None
    with _cache_lock:
        _cache[int(user_id)] = (now, value)
    return value


def path_is_allowed_when_expired(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _ALLOWED_PREFIXES)


def is_blocked(db_path, user: dict[str, Any] | None, path: str) -> bool:
    if not isinstance(user, dict) or user.get("user_id") is None:
        return False
    if path_is_allowed_when_expired(path) or is_super_admin(user):
        return False
    ends = _parse(_cached_ends_at(db_path, int(user["user_id"])))
    return ends is not None and ends <= _now()


def generate_codes(
    db_path,
    creator: dict[str, Any],
    duration_days: int,
    count: int = 1,
    for_username: str | None = None,
    note: str | None = None,
) -> list[dict[str, Any]]:
    duration_days = int(duration_days or 0)
    if duration_days < 1 or duration_days > MAX_CODE_DAYS:
        raise ValueError(f"duration_days must be between 1 and {MAX_CODE_DAYS}")
    count = max(1, min(int(count or 1), MAX_BATCH))
    for_username = (for_username or "").strip() or None
    now = _now().isoformat()
    out: list[dict[str, Any]] = []
    with _connect(db_path) as conn:
        if for_username and not conn.execute(
            "SELECT 1 FROM users WHERE lower(username) = lower(?)", (for_username,)
        ).fetchone():
            raise ValueError(f"No login named '{for_username}'")
        while len(out) < count:
            code = "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))
            try:
                cur = conn.execute(
                    """INSERT INTO subscription_codes
                       (code, duration_days, for_username, note, created_by_user_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (code, duration_days, for_username, (note or "").strip() or None,
                     creator.get("user_id"), now),
                )
            except sqlite3.IntegrityError:
                continue
            row = conn.execute(
                "SELECT * FROM subscription_codes WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            out.append(_code_to_dict(row))
        conn.commit()
    return out


def _code_to_dict(row) -> dict[str, Any]:
    if row["revoked_at"]:
        status = "revoked"
    elif row["redeemed_at"]:
        status = "used"
    else:
        status = "unused"
    return {
        "id": row["id"],
        "code": display_code(row["code"]),
        "duration_days": row["duration_days"],
        "for_username": row["for_username"],
        "note": row["note"],
        "created_at": row["created_at"],
        "redeemed_by_username": row["redeemed_by_username"],
        "redeemed_at": row["redeemed_at"],
        "revoked_at": row["revoked_at"],
        "status": status,
    }


def list_codes(db_path, limit: int = 200) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM subscription_codes ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    return [_code_to_dict(r) for r in rows]


def revoke_code(db_path, code: str) -> bool:
    with _connect(db_path) as conn:
        cur = conn.execute(
            """UPDATE subscription_codes SET revoked_at = ?
               WHERE code = ? AND revoked_at IS NULL AND redeemed_at IS NULL""",
            (_now().isoformat(), normalize_code(code)),
        )
        conn.commit()
        return cur.rowcount > 0


def redeem_code(db_path, user_id: int, username: str, code: str) -> dict[str, Any]:
    """Use a code on this login. Raises ValueError when the code can't be used."""
    norm = normalize_code(code)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM subscription_codes WHERE code = ?", (norm,)).fetchone()
        if row is None:
            conn.rollback()
            raise ValueError("Code not found — check it and try again")
        if row["revoked_at"]:
            conn.rollback()
            raise ValueError("This code has been cancelled")
        if row["redeemed_at"]:
            conn.rollback()
            raise ValueError("This code has already been used")
        if row["for_username"] and row["for_username"].lower() != (username or "").lower():
            conn.rollback()
            raise ValueError("This code was made for a different login")
        user = conn.execute(
            "SELECT trial_ends_at, paid_until FROM users WHERE id = ?", (int(user_id),)
        ).fetchone()
        if user is None:
            conn.rollback()
            raise ValueError("Login not found")
        current = access_ends_at(user["trial_ends_at"], user["paid_until"])
        start = current if current is not None and current > now else now
        new_end = (start + timedelta(days=int(row["duration_days"]))).isoformat()
        conn.execute("UPDATE users SET paid_until = ? WHERE id = ?", (new_end, int(user_id)))
        conn.execute(
            """UPDATE subscription_codes
               SET redeemed_by_user_id = ?, redeemed_by_username = ?, redeemed_at = ?
               WHERE id = ?""",
            (int(user_id), username, now.isoformat(), row["id"]),
        )
        conn.commit()
    _invalidate(user_id)
    return {"duration_days": int(row["duration_days"]), **status_for_user(db_path, user_id)}


def list_user_plans(db_path) -> list[dict[str, Any]]:
    """Every login with its plan status, for the super admin's overview."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT id, username, role, workspace_id, created_at, trial_ends_at, paid_until
               FROM users
               WHERE lower(username) NOT LIKE 'archived_dup_%'
               ORDER BY id"""
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "user_id": r["id"],
            "username": r["username"],
            "role": r["role"],
            "workspace_id": r["workspace_id"],
            "created_at": r["created_at"],
            **_status_from_row(r),
        })
    return out


def set_unlimited(db_path, user_id: int) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE users SET trial_ends_at = NULL, paid_until = NULL WHERE id = ?", (int(user_id),)
        )
        conn.commit()
    _invalidate(user_id)

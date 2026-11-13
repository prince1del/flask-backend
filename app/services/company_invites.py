"""Company invite codes: let a new login join an EXISTING company's workspace.

Self-signup (POST /api/v1/signup) normally creates a brand-new company. With a
valid invite code the new login is created inside the inviter's workspace
instead, with the role the inviter chose — so a company's whole team shares one
workspace_id and sees the same business data.

Codes are 8 characters from an unambiguous alphabet, shown as ``ABCD-EFGH``,
stored without the dash. Each code has a role, a use limit and an expiry.
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

# No 0/O/1/I/L — codes are read out over the phone.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8

# Roles a new teammate can be invited as.
INVITE_ROLES = ("sales_executive", "distributor", "retailer", "hop_admin")
# Only a company admin may hand out the admin role.
_ADMIN_ONLY_ROLES = frozenset({"hop_admin"})
_ADMIN_ROLES = frozenset({"hop_admin", "admin"})

MAX_USES_LIMIT = 100
MAX_EXPIRY_DAYS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_code(code: str | None) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def display_code(code: str) -> str:
    c = normalize_code(code)
    return f"{c[:4]}-{c[4:]}" if len(c) == CODE_LENGTH else c


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL,
            role TEXT NOT NULL,
            max_uses INTEGER NOT NULL DEFAULT 1,
            uses INTEGER NOT NULL DEFAULT 0,
            note TEXT,
            created_by_user_id INTEGER,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_company_invites_ws ON company_invites(workspace_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company_invite_redemptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invite_id INTEGER NOT NULL REFERENCES company_invites(id),
            user_id INTEGER NOT NULL,
            username TEXT,
            redeemed_at TEXT NOT NULL
        )
        """
    )


def _connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_tables(conn)
    return conn


def company_name_for_workspace(db, workspace_id: str) -> str | None:
    """Readable company name for a workspace (None for unclassified silos)."""
    try:
        company_id = db.get_company_id_for_workspace(workspace_id)
    except Exception:
        return None
    if company_id == 3:
        return None
    with sqlite3.connect(str(db.db_path)) as conn:
        row = conn.execute("SELECT name FROM companies WHERE id = ?", (company_id,)).fetchone()
    return str(row[0]) if row and row[0] else None


def can_manage_invites(db, user: dict[str, Any]) -> bool:
    """Who may invite teammates into their own workspace.

    The company owner, the platform workspace owner, a company admin
    (hop_admin), or — for companies that never recorded an owner — the
    earliest active login of that workspace.
    """
    if not isinstance(user, dict):
        return False
    if user.get("is_workspace_owner"):
        return True
    if str(user.get("role") or "") in _ADMIN_ROLES:
        return True
    uid = user.get("user_id")
    workspace_id = str(user.get("workspace_id") or "")
    if uid is None or not workspace_id or workspace_id == "default":
        return False
    company_id = db.get_company_id_for_workspace(workspace_id)
    with sqlite3.connect(str(db.db_path)) as conn:
        owner = None
        if company_id != 3:
            row = conn.execute(
                "SELECT owner_user_id FROM companies WHERE id = ?", (company_id,)
            ).fetchone()
            owner = row[0] if row else None
        if owner is not None:
            return int(owner) == int(uid)
        first = conn.execute(
            """SELECT id FROM users
               WHERE workspace_id = ? AND IFNULL(status, 'active') = 'active'
               ORDER BY created_at, id LIMIT 1""",
            (workspace_id,),
        ).fetchone()
    return bool(first and int(first[0]) == int(uid))


def allowed_roles_for(user: dict[str, Any]) -> list[str]:
    is_admin = bool(user.get("is_workspace_owner")) or str(user.get("role") or "") in _ADMIN_ROLES
    return [r for r in INVITE_ROLES if is_admin or r not in _ADMIN_ONLY_ROLES]


def _row_to_dict(row: sqlite3.Row, now: datetime) -> dict[str, Any]:
    expires = datetime.fromisoformat(row["expires_at"])
    if row["revoked_at"]:
        status = "revoked"
    elif row["uses"] >= row["max_uses"]:
        status = "used_up"
    elif expires <= now:
        status = "expired"
    else:
        status = "active"
    return {
        "id": row["id"],
        "code": display_code(row["code"]),
        "role": row["role"],
        "max_uses": row["max_uses"],
        "uses": row["uses"],
        "note": row["note"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "revoked_at": row["revoked_at"],
        "status": status,
    }


def create_invite(
    db,
    user: dict[str, Any],
    role: str,
    max_uses: int = 1,
    expires_in_days: int = 7,
    note: str | None = None,
) -> dict[str, Any]:
    role = (role or "").strip()
    if role not in allowed_roles_for(user):
        raise ValueError(f"role must be one of: {', '.join(allowed_roles_for(user))}")
    max_uses = max(1, min(int(max_uses or 1), MAX_USES_LIMIT))
    expires_in_days = max(1, min(int(expires_in_days or 7), MAX_EXPIRY_DAYS))
    workspace_id = str(user.get("workspace_id") or "")
    if not workspace_id or workspace_id == "default":
        raise ValueError("Your login has no company workspace to invite into")
    now = _now()
    expires_at = (now + timedelta(days=expires_in_days)).isoformat()
    with _connect(db.db_path) as conn:
        for _ in range(10):
            code = "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))
            try:
                cur = conn.execute(
                    """INSERT INTO company_invites
                       (code, workspace_id, role, max_uses, uses, note,
                        created_by_user_id, created_at, expires_at)
                       VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)""",
                    (code, workspace_id, role, max_uses, (note or "").strip() or None,
                     user.get("user_id"), now.isoformat(), expires_at),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM company_invites WHERE id = ?", (cur.lastrowid,)
                ).fetchone()
                return _row_to_dict(row, now)
            except sqlite3.IntegrityError:
                continue
    raise RuntimeError("Could not generate a unique invite code")


def list_invites(db, workspace_id: str) -> list[dict[str, Any]]:
    now = _now()
    with _connect(db.db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM company_invites WHERE workspace_id = ? ORDER BY id DESC",
            (workspace_id,),
        ).fetchall()
        out = []
        for row in rows:
            item = _row_to_dict(row, now)
            joined = conn.execute(
                """SELECT user_id, username, redeemed_at FROM company_invite_redemptions
                   WHERE invite_id = ? ORDER BY id""",
                (row["id"],),
            ).fetchall()
            item["joined"] = [dict(j) for j in joined]
            out.append(item)
    return out


def revoke_invite(db, workspace_id: str, code: str) -> bool:
    with _connect(db.db_path) as conn:
        cur = conn.execute(
            """UPDATE company_invites SET revoked_at = ?
               WHERE code = ? AND workspace_id = ? AND revoked_at IS NULL""",
            (_now().isoformat(), normalize_code(code), workspace_id),
        )
        conn.commit()
        return cur.rowcount > 0


def check_invite(db, code: str) -> dict[str, Any] | None:
    """Public preview for the sign-up form: company and role, if usable."""
    norm = normalize_code(code)
    if len(norm) != CODE_LENGTH:
        return None
    now = _now()
    with _connect(db.db_path) as conn:
        row = conn.execute("SELECT * FROM company_invites WHERE code = ?", (norm,)).fetchone()
    if row is None:
        return None
    item = _row_to_dict(row, now)
    if item["status"] != "active":
        return {"valid": False, "status": item["status"]}
    return {
        "valid": True,
        "status": "active",
        "role": row["role"],
        "company_name": company_name_for_workspace(db, row["workspace_id"]),
    }


def claim_invite(db, code: str) -> dict[str, Any]:
    """Atomically take one use of an invite. Raises ValueError if unusable.

    Pair with release_invite() if the login could not be created afterwards.
    """
    norm = normalize_code(code)
    now = _now().isoformat()
    with _connect(db.db_path) as conn:
        cur = conn.execute(
            """UPDATE company_invites SET uses = uses + 1
               WHERE code = ? AND revoked_at IS NULL AND uses < max_uses AND expires_at > ?""",
            (norm, now),
        )
        conn.commit()
        if cur.rowcount != 1:
            raise ValueError("This invite code is invalid, expired or already used")
        row = conn.execute("SELECT * FROM company_invites WHERE code = ?", (norm,)).fetchone()
    return {"id": row["id"], "workspace_id": row["workspace_id"], "role": row["role"]}


def release_invite(db, invite_id: int) -> None:
    with _connect(db.db_path) as conn:
        conn.execute(
            "UPDATE company_invites SET uses = MAX(uses - 1, 0) WHERE id = ?", (int(invite_id),)
        )
        conn.commit()


def record_redemption(db, invite_id: int, user_id: int, username: str) -> None:
    with _connect(db.db_path) as conn:
        conn.execute(
            """INSERT INTO company_invite_redemptions (invite_id, user_id, username, redeemed_at)
               VALUES (?, ?, ?, ?)""",
            (int(invite_id), int(user_id), username, _now().isoformat()),
        )
        conn.commit()


def list_members(db, workspace_id: str) -> list[dict[str, Any]]:
    db.ensure_user_profile_columns()
    with sqlite3.connect(str(db.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        pick = [c for c in ("id", "username", "full_name", "role", "status", "created_at") if c in cols]
        rows = conn.execute(
            f"""SELECT {', '.join(pick)} FROM users
                WHERE workspace_id = ? AND lower(username) NOT LIKE 'archived_dup_%'
                ORDER BY created_at, id""",
            (workspace_id,),
        ).fetchall()
    return [dict(r) for r in rows]

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
MAX_EXPIRY_DAYS = 30

# Team size is a paid add-on: a company may hold only ``companies.max_members``
# logins (owner included). NULL = the default — no limit for the founder's own
# companies, just the owner for everyone else. 0 = no limit (set by the super
# admin). New teammates need a seat; the super admin grants seats after payment.
_TEAM_UNLIMITED_COMPANY_IDS = frozenset({1, 2})
MAX_TEAM_REQUEST = 500


class TeamLimitError(ValueError):
    pass


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS team_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            workspace_id TEXT NOT NULL,
            user_id INTEGER,
            username TEXT,
            members INTEGER NOT NULL,
            note TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            decided_at TEXT
        )
        """
    )
    company_cols = {r[1] for r in conn.execute("PRAGMA table_info(companies)").fetchall()}
    if company_cols and "max_members" not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN max_members INTEGER")
        conn.commit()


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


def team_limit(db, workspace_id: str) -> int | None:
    """Most logins this workspace's company may have; None = no limit."""
    company_id = db.get_company_id_for_workspace(workspace_id)
    with _connect(db.db_path) as conn:
        row = conn.execute("SELECT max_members FROM companies WHERE id = ?", (company_id,)).fetchone()
    value = row["max_members"] if row else None
    if value is None:
        return None if company_id in _TEAM_UNLIMITED_COMPANY_IDS else 1
    return None if int(value) <= 0 else int(value)


def member_count(db, workspace_id: str) -> int:
    with sqlite3.connect(str(db.db_path)) as conn:
        row = conn.execute(
            """SELECT COUNT(*) FROM users
               WHERE workspace_id = ? AND IFNULL(status, 'active') = 'active'
                 AND lower(username) NOT LIKE 'archived_dup_%'""",
            (workspace_id,),
        ).fetchone()
    return int(row[0] or 0)


def _open_invite_places(conn: sqlite3.Connection, workspace_id: str) -> int:
    row = conn.execute(
        """SELECT COALESCE(SUM(max_uses - uses), 0) FROM company_invites
           WHERE workspace_id = ? AND revoked_at IS NULL AND uses < max_uses AND expires_at > ?""",
        (workspace_id, _now().isoformat()),
    ).fetchone()
    return int(row[0] or 0)


def seats_left(db, workspace_id: str) -> int | None:
    """Teammates that can still be invited (open invite codes hold a place)."""
    limit = team_limit(db, workspace_id)
    if limit is None:
        return None
    with _connect(db.db_path) as conn:
        held = _open_invite_places(conn, workspace_id)
    return max(0, limit - member_count(db, workspace_id) - held)


def _pending_request(conn: sqlite3.Connection, company_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT id, members, note, created_at FROM team_requests
           WHERE company_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    return dict(row) if row else None


def team_status(db, workspace_id: str) -> dict[str, Any]:
    company_id = db.get_company_id_for_workspace(workspace_id)
    with _connect(db.db_path) as conn:
        pending = _pending_request(conn, company_id)
    return {
        "team_limit": team_limit(db, workspace_id),
        "member_count": member_count(db, workspace_id),
        "seats_left": seats_left(db, workspace_id),
        "team_request": pending,
    }


def request_team(db, user: dict[str, Any], workspace_id: str, members: int, note: str | None) -> dict[str, Any]:
    """Ask NEXORA for team places (``members`` = people besides the owner)."""
    try:
        members = int(members)
    except (TypeError, ValueError):
        raise ValueError("Enter how many people you want to add") from None
    if members < 1 or members > MAX_TEAM_REQUEST:
        raise ValueError(f"Ask for 1 to {MAX_TEAM_REQUEST} people")
    company_id = db.get_company_id_for_workspace(workspace_id)
    now = _now().isoformat()
    note = (note or "").strip()[:300] or None
    with _connect(db.db_path) as conn:
        pending = _pending_request(conn, company_id)
        if pending:
            conn.execute(
                "UPDATE team_requests SET members = ?, note = ?, created_at = ? WHERE id = ?",
                (members, note, now, pending["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO team_requests
                   (company_id, workspace_id, user_id, username, members, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (company_id, workspace_id, user.get("user_id"), user.get("username"), members, note, now),
            )
        conn.commit()
        return _pending_request(conn, company_id) or {}


def list_team_requests(db) -> list[dict[str, Any]]:
    """Super admin: every team request, newest first, with the company's current size."""
    with _connect(db.db_path) as conn:
        rows = conn.execute(
            """SELECT r.*, c.name AS company_name, c.max_members
               FROM team_requests r LEFT JOIN companies c ON c.id = r.company_id
               ORDER BY CASE r.status WHEN 'pending' THEN 0 ELSE 1 END, r.id DESC LIMIT 100"""
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["member_count"] = member_count(db, row["workspace_id"])
        item["team_limit"] = team_limit(db, row["workspace_id"])
        out.append(item)
    return out


def set_team_size(db, company_id: int, max_members: int) -> None:
    """Super admin: allow ``max_members`` logins in a company (0 = no limit)."""
    max_members = int(max_members)
    if max_members < 0:
        raise ValueError("max_members must be 0 (no limit) or more")
    now = _now().isoformat()
    with _connect(db.db_path) as conn:
        cur = conn.execute("UPDATE companies SET max_members = ? WHERE id = ?", (max_members, int(company_id)))
        if cur.rowcount != 1:
            raise ValueError("Company not found")
        conn.execute(
            """UPDATE team_requests SET status = 'approved', decided_at = ?
               WHERE company_id = ? AND status = 'pending'""",
            (now, int(company_id)),
        )
        conn.commit()


def decline_team_request(db, request_id: int) -> bool:
    with _connect(db.db_path) as conn:
        cur = conn.execute(
            """UPDATE team_requests SET status = 'declined', decided_at = ?
               WHERE id = ? AND status = 'pending'""",
            (_now().isoformat(), int(request_id)),
        )
        conn.commit()
        return cur.rowcount > 0


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
    left = seats_left(db, workspace_id)
    if left is not None:
        if left <= 0:
            raise TeamLimitError(
                "Your plan has no free team places. Ask NEXORA to add team members."
            )
        max_uses = min(max_uses, left)
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
    limit = team_limit(db, row["workspace_id"])
    if limit is not None and member_count(db, row["workspace_id"]) >= limit:
        return {"valid": False, "status": "company_full"}
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
        found = conn.execute("SELECT workspace_id FROM company_invites WHERE code = ?", (norm,)).fetchone()
    if found is not None:
        limit = team_limit(db, found["workspace_id"])
        if limit is not None and member_count(db, found["workspace_id"]) >= limit:
            raise ValueError(
                "This company has no free places left. Ask your company owner to contact NEXORA."
            )
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

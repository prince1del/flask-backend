"""What each team member may open, chosen by the company owner — and switching a login off.

* The owner/admin picks features for every invite (``company_invites.features``);
  the member gets them on joining (``member_access`` row).
* From the Team dashboard the owner/admin can change a member's features or
  block the login at any time. A blocked login cannot sign in and every API call
  answers 403 ACCOUNT_BLOCKED (so an open app logs out at once).
* Logins without a ``member_access`` row (owners, House of Prizm / Bombay Dyeing
  staff, everyone from before this feature) keep every feature.

Isolation: a manager only ever sees and changes members of **their own
workspace**; owners, admins and the super admin can never be restricted by
another member.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any

# Order = order on screen. Prefixes are whole URL segments under /api/v1.
FEATURES: list[dict[str, Any]] = [
    {"key": "order_desk", "label": "Order Desk (FO, SO, CI)",
     "prefixes": ["filled-orders", "order-fulfillment", "fulfillment", "seasons", "so-mail", "sales-orders",
                  "order-sheets", "order-reconciliation", "orders", "mail-sync"]},
    {"key": "distributor_zone", "label": "Distributor Zone (payments, Hisaab, ledger)",
     "prefixes": ["distributor-payments", "distributor-ledger", "statement-of-account",
                  "distributor-secondary-sales", "distributor-grievances"]},
    {"key": "market_visit", "label": "Market Visit", "prefixes": ["dsr-market"]},
    {"key": "todo", "label": "To-Do", "prefixes": ["personal-todos"]},
    {"key": "target", "label": "Target vs Achievement", "prefixes": ["target-achievement"]},
    {"key": "customers", "label": "Customers", "prefixes": ["parties", "party-matching"]},
    {"key": "articles", "label": "Article Master", "prefixes": ["article-master"]},
    {"key": "catalogues", "label": "Catalogues", "prefixes": []},
    {"key": "pjp", "label": "Journey Plan (PJP)", "prefixes": ["pjp"]},
    {"key": "call_list", "label": "Call List", "prefixes": ["call-lists"]},
    {"key": "ask_nexora", "label": "Ask Nexora (AI)", "prefixes": ["ai", "ask-nexora", "ai-assistant"]},
    {"key": "watch", "label": "Smart Watch", "prefixes": ["watch/pair"]},
    # Attendance and location are separate: a freelancer can have neither,
    # office staff attendance without tracking.
    {"key": "attendance", "label": "Attendance (Start/End day)",
     "prefixes": ["team/duty", "team/attendance/me"]},
    {"key": "live_location", "label": "Live location while on duty",
     "prefixes": ["team/location"]},
]
FEATURE_KEYS = [f["key"] for f in FEATURES]
_PREFIX_TO_KEY = sorted(
    ((f"/api/v1/{p}", f["key"]) for f in FEATURES for p in f["prefixes"]),
    key=lambda x: -len(x[0]),
)
# A blocked login may still sign out.
_BLOCKED_ALLOWED = ("/api/v1/auth/logout",)

_CACHE_TTL = 30
_cache: dict[int, tuple[float, dict[str, Any] | None]] = {}
_lock = threading.Lock()


def catalog() -> list[dict[str, str]]:
    return [{"key": f["key"], "label": f["label"]} for f in FEATURES]


def clean_features(features: Any) -> list[str]:
    """Only known keys, in catalog order. Raises ValueError for anything else."""
    if features is None:
        return list(FEATURE_KEYS)
    if not isinstance(features, (list, tuple)):
        raise ValueError("features must be a list")
    unknown = [f for f in features if f not in FEATURE_KEYS]
    if unknown:
        raise ValueError(f"Unknown feature: {', '.join(map(str, unknown))}")
    chosen = set(features)
    return [k for k in FEATURE_KEYS if k in chosen]


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS member_access (
            user_id INTEGER PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            features TEXT,
            blocked INTEGER NOT NULL DEFAULT 0,
            blocked_at TEXT,
            updated_by INTEGER,
            updated_at TEXT
        )
        """
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(company_invites)").fetchall()}
    if cols and "features" not in cols:
        conn.execute("ALTER TABLE company_invites ADD COLUMN features TEXT")
    conn.commit()


def _connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def invalidate(user_id: int | None = None) -> None:
    with _lock:
        if user_id is None:
            _cache.clear()
        else:
            _cache.pop(int(user_id), None)


def _row(db_path, user_id: int) -> dict[str, Any] | None:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(int(user_id))
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    with _connect(db_path) as conn:
        r = conn.execute("SELECT * FROM member_access WHERE user_id = ?", (int(user_id),)).fetchone()
    value = None
    if r is not None:
        feats = json.loads(r["features"]) if r["features"] else list(FEATURE_KEYS)
        value = {"features": feats, "blocked": bool(r["blocked"]), "workspace_id": r["workspace_id"]}
    with _lock:
        _cache[int(user_id)] = (now, value)
    return value


def _unrestricted(user: dict[str, Any]) -> bool:
    from app.services import company_invites as ci
    from app.services import subscriptions as subs

    if subs.is_super_admin(user) or user.get("is_workspace_owner"):
        return True
    return str(user.get("role") or "") in ci._ADMIN_ROLES


def feature_for_path(path: str) -> str | None:
    for prefix, key in _PREFIX_TO_KEY:
        if path == prefix or path.startswith(prefix + "/"):
            return key
    return None


def denied(db_path, user: dict[str, Any] | None, path: str) -> tuple[str, str] | None:
    """(code, message) when this call must be refused, else None."""
    if not isinstance(user, dict) or user.get("user_id") is None:
        return None
    from app.services import subscriptions as subs

    if subs.is_super_admin(user) or user.get("is_workspace_owner"):
        return None
    acc = _row(db_path, int(user["user_id"]))
    if acc is None:
        return None
    if acc["blocked"]:  # applies to a blocked admin too
        if path in _BLOCKED_ALLOWED:
            return None
        return ("ACCOUNT_BLOCKED", "Your company has switched off this login. Contact your company owner.")
    if _unrestricted(user):
        return None
    key = feature_for_path(path)
    if key and key not in acc["features"]:
        label = next(f["label"] for f in FEATURES if f["key"] == key)
        return ("FEATURE_OFF", f"{label} is not open for your login. Ask your company owner.")
    return None


def is_blocked(db_path, user_id: int) -> bool:
    acc = _row(db_path, int(user_id))
    return bool(acc and acc["blocked"])


def features_for(db_path, user: dict[str, Any]) -> list[str] | None:
    """Features this login may open; None = everything (owner, admin, older logins)."""
    if not isinstance(user, dict) or user.get("user_id") is None or _unrestricted(user):
        return None
    acc = _row(db_path, int(user["user_id"]))
    return None if acc is None else list(acc["features"])


def grant_on_join(db_path, user_id: int, workspace_id: str, features: list[str] | None) -> None:
    """New member from an invite: store the features the inviter chose."""
    feats = clean_features(features)
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO member_access (user_id, workspace_id, features, blocked, updated_at)
               VALUES (?, ?, ?, 0, ?)
               ON CONFLICT(user_id) DO UPDATE SET workspace_id = excluded.workspace_id,
                 features = excluded.features, updated_at = excluded.updated_at""",
            (int(user_id), workspace_id, json.dumps(feats), _now()),
        )
        conn.commit()
    invalidate(user_id)


def _member_in_my_workspace(conn: sqlite3.Connection, manager: dict[str, Any], member_id: int):
    """The member row — only if it is in the manager's own workspace."""
    ws = str(manager.get("workspace_id") or "")
    row = conn.execute(
        "SELECT id, username, role, workspace_id, IFNULL(is_workspace_owner, 0) AS is_owner FROM users WHERE id = ?",
        (int(member_id),),
    ).fetchone()
    if row is None or not ws or row["workspace_id"] != ws:
        raise LookupError("Member not found in your company")
    return row


def update_member(db, manager: dict[str, Any], member_id: int,
                  features: list[str] | None = None, blocked: bool | None = None) -> dict[str, Any]:
    """Owner/admin changes one teammate's features and/or blocks / unblocks the login."""
    from app.services import company_invites as ci

    if not ci.can_manage_invites(db, manager):
        raise PermissionError("Only your company's owner or admin can change team access")
    if int(member_id) == int(manager.get("user_id") or 0):
        raise PermissionError("You cannot change your own access")
    with _connect(db.db_path) as conn:
        member = _member_in_my_workspace(conn, manager, member_id)
        company_owner = None
        try:
            r = conn.execute(
                """SELECT c.owner_user_id FROM workspace_registry w JOIN companies c ON c.id = w.company_id
                   WHERE w.workspace_id = ?""", (member["workspace_id"],)).fetchone()
            company_owner = r[0] if r else None
        except sqlite3.OperationalError:
            pass
        if member["is_owner"] or (company_owner is not None and int(company_owner) == int(member["id"])):
            raise PermissionError("The company owner's access cannot be changed")
        if str(member["role"] or "") in ci._ADMIN_ROLES and not (
            manager.get("is_workspace_owner") or (company_owner and int(company_owner) == int(manager["user_id"]))
        ):
            raise PermissionError("Only the company owner can change an admin's access")
        cur = conn.execute("SELECT features, blocked FROM member_access WHERE user_id = ?", (int(member_id),)).fetchone()
        feats = clean_features(features) if features is not None else (
            json.loads(cur["features"]) if cur and cur["features"] else list(FEATURE_KEYS))
        block = (bool(cur["blocked"]) if cur else False) if blocked is None else bool(blocked)
        conn.execute(
            """INSERT INTO member_access (user_id, workspace_id, features, blocked, blocked_at, updated_by, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET features = excluded.features, blocked = excluded.blocked,
                 blocked_at = excluded.blocked_at, updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
            (int(member_id), member["workspace_id"], json.dumps(feats), 1 if block else 0,
             _now() if block else None, int(manager["user_id"]), _now()),
        )
        conn.commit()
    invalidate(member_id)
    return {"user_id": int(member_id), "features": feats, "blocked": block}


def team_dashboard(db, manager: dict[str, Any]) -> list[dict[str, Any]]:
    """Every login in the manager's workspace with its features and blocked flag."""
    from app.services import company_invites as ci

    ws = str(manager.get("workspace_id") or "")
    members = ci.list_members(db, ws)
    with _connect(db.db_path) as conn:
        rows = {r["user_id"]: r for r in conn.execute(
            "SELECT * FROM member_access WHERE workspace_id = ?", (ws,)).fetchall()}
        owner_id = None
        try:
            r = conn.execute(
                """SELECT c.owner_user_id FROM workspace_registry w JOIN companies c ON c.id = w.company_id
                   WHERE w.workspace_id = ?""", (ws,)).fetchone()
            owner_id = r[0] if r else None
        except sqlite3.OperationalError:
            pass
    from app.services import login_approval

    devices = login_approval.device_of(db.db_path, ws)
    extra: dict[int, tuple] = {}
    with _connect(db.db_path) as conn:
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
            pick = [c for c in ("designation", "active_session_at", "email", "phone") if c in cols]
            if pick:
                for r in conn.execute(f"SELECT id, {', '.join(pick)} FROM users WHERE workspace_id = ?", (ws,)).fetchall():
                    extra[int(r[0])] = dict(zip(pick, r[1:]))
        except sqlite3.OperationalError:
            pass
    out = []
    for m in members:
        acc = rows.get(m["id"])
        is_owner = owner_id is not None and int(owner_id) == int(m["id"])
        out.append({
            **m,
            "is_owner": is_owner,
            "is_me": int(m["id"]) == int(manager.get("user_id") or 0),
            "features": None if (is_owner or acc is None) else (
                json.loads(acc["features"]) if acc["features"] else list(FEATURE_KEYS)),
            "blocked": bool(acc["blocked"]) if acc else False,
            "device": devices.get(m["id"]),
            "designation": (extra.get(int(m["id"])) or {}).get("designation"),
            "last_active": (extra.get(int(m["id"])) or {}).get("active_session_at"),
            "email": (extra.get(int(m["id"])) or {}).get("email") or m.get("email"),
            "phone": (extra.get(int(m["id"])) or {}).get("phone"),
        })
    return out


def _check_manager_of(db, manager: dict[str, Any], member_id: int, allow_self: bool = False) -> dict[str, Any]:
    """Same rules as update_member: owner/admin of the same company; owner's login is protected."""
    from app.services import company_invites as ci

    if not ci.can_manage_invites(db, manager):
        raise PermissionError("Only your company's owner or admin can do this")
    if not allow_self and int(member_id) == int(manager.get("user_id") or 0):
        raise PermissionError("Use your own Profile for this")
    with _connect(db.db_path) as conn:
        member = _member_in_my_workspace(conn, manager, member_id)
        company_owner = None
        try:
            r = conn.execute(
                """SELECT c.owner_user_id FROM workspace_registry w JOIN companies c ON c.id = w.company_id
                   WHERE w.workspace_id = ?""", (member["workspace_id"],)).fetchone()
            company_owner = r[0] if r else None
        except sqlite3.OperationalError:
            pass
    is_owner = member["is_owner"] or (company_owner is not None and int(company_owner) == int(member["id"]))
    if is_owner and int(member_id) != int(manager.get("user_id") or 0):
        raise PermissionError("The company owner's login cannot be changed by others")
    if str(member["role"] or "") in ci._ADMIN_ROLES and not (
        manager.get("is_workspace_owner") or (company_owner and int(company_owner) == int(manager["user_id"]))
    ) and int(member_id) != int(manager.get("user_id") or 0):
        raise PermissionError("Only the company owner can change an admin")
    return member


def set_designation(db, manager: dict[str, Any], member_id: int, designation: str) -> dict[str, Any]:
    """Owner/admin sets a teammate's job title (shown under their name)."""
    _check_manager_of(db, manager, member_id, allow_self=True)
    prof = db.update_user_profile(int(member_id), designation=str(designation or "")[:60])
    return {"id": int(member_id), "designation": prof.get("designation")}


def reset_password(db, manager: dict[str, Any], member_id: int, new_password: str) -> dict[str, Any]:
    """Owner/admin gives a teammate a new password (e.g. they forgot it and have no email / secret question).
    Their phone is signed out, so the old password stops working everywhere."""
    _check_manager_of(db, manager, member_id)
    pw = str(new_password or "").strip()
    if len(pw) < 8:
        raise ValueError("Password must be at least 8 characters")
    db.update_user_profile(int(member_id), password=pw)
    try:
        from app.services import login_approval

        login_approval.sign_out_member(db, manager, int(member_id))
    except Exception:
        pass
    return {"id": int(member_id), "password_reset": True}

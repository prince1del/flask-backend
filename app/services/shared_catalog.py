"""One Article Master per company.

Article Master rows are keyed by ``user_id``. Inside a real company (any
workspace registered to a company other than "Unclassified / Legacy") every
login reads the same catalog — the one owned by the company's catalog owner —
and only the company's owner/admin may change it. Logins outside a company
keep their own private catalog, as before.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.services import company_invites

_UNCLASSIFIED_COMPANY_ID = 3


class CatalogReadOnly(PermissionError):
    pass


def _company_id(conn: sqlite3.Connection, workspace_id: str) -> int:
    try:
        row = conn.execute(
            "SELECT company_id FROM workspace_registry WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return _UNCLASSIFIED_COMPANY_ID
    return int(row[0]) if row else _UNCLASSIFIED_COMPANY_ID


def _catalog_owner(conn: sqlite3.Connection, workspace_id: str, company_id: int) -> int | None:
    try:
        row = conn.execute(
            """SELECT a.user_id FROM article_master a
               JOIN users u ON u.id = a.user_id
               WHERE u.workspace_id = ?
               GROUP BY a.user_id ORDER BY COUNT(*) DESC, a.user_id LIMIT 1""",
            (workspace_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row:
        return int(row[0])
    owner = conn.execute(
        """SELECT c.owner_user_id FROM companies c
           JOIN users u ON u.id = c.owner_user_id
           WHERE c.id = ? AND u.workspace_id = ?""",
        (company_id, workspace_id),
    ).fetchone()
    if owner and owner[0] is not None:
        return int(owner[0])
    first = conn.execute(
        """SELECT id FROM users WHERE workspace_id = ? AND IFNULL(status, 'active') = 'active'
           ORDER BY created_at, id LIMIT 1""",
        (workspace_id,),
    ).fetchone()
    return int(first[0]) if first else None


def catalog_scope(db, user: dict[str, Any], workspace_id: str) -> dict[str, Any]:
    """Whose Article Master this login uses and whether it may change it."""
    caller = int(user["user_id"])
    with sqlite3.connect(str(db.db_path)) as conn:
        company_id = _company_id(conn, workspace_id)
        if company_id == _UNCLASSIFIED_COMPANY_ID:
            return {"user_id": caller, "can_edit": True, "shared": False}
        owner = _catalog_owner(conn, workspace_id, company_id) or caller
    if owner == caller:
        return {"user_id": caller, "can_edit": True, "shared": False}
    can_edit = company_invites.can_manage_invites(db, {**user, "workspace_id": workspace_id})
    return {"user_id": owner, "can_edit": can_edit, "shared": True}


def catalog_user_id(db, user: dict[str, Any], workspace_id: str) -> int:
    return int(catalog_scope(db, user, workspace_id)["user_id"])

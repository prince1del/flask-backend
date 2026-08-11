"""Ask Nexora troubleshoot log — questions Nexora couldn't answer, kept so
they can be taught later and cleared from Settings once fixed."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from app.routes.auth import get_workspace_id, require_jwt_auth

ask_nexora_troubleshoot_bp = Blueprint(
    "ask_nexora_troubleshoot", __name__, url_prefix="/api/v1/ask-nexora/troubleshoot"
)


def _db_path() -> str:
    return current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ask_nexora_unresolved_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            user_id INTEGER,
            query_text TEXT NOT NULL,
            hit_count INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ask_nexora_unresolved_ws "
        "ON ask_nexora_unresolved_queries(workspace_id, last_seen_at)"
    )


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "query_text": row["query_text"],
        "hit_count": row["hit_count"],
        "created_at": row["created_at"],
        "last_seen_at": row["last_seen_at"],
    }


def log_unresolved_query(workspace_id: str, user_id: int | None, query_text: str) -> None:
    """Record a question Ask Nexora couldn't answer. Repeats of the same
    question (case-insensitive) bump hit_count/last_seen_at instead of
    creating duplicate rows, so the troubleshoot list doesn't fill up with
    the same miss asked several times."""
    query_text = (query_text or "").strip()
    if not query_text or not workspace_id:
        return
    now = _now_iso()
    try:
        with sqlite3.connect(_db_path()) as conn:
            _ensure_table(conn)
            existing = conn.execute(
                "SELECT id FROM ask_nexora_unresolved_queries "
                "WHERE workspace_id = ? AND LOWER(query_text) = LOWER(?)",
                (workspace_id, query_text),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE ask_nexora_unresolved_queries "
                    "SET hit_count = hit_count + 1, last_seen_at = ? WHERE id = ?",
                    (now, existing[0]),
                )
            else:
                conn.execute(
                    "INSERT INTO ask_nexora_unresolved_queries "
                    "(workspace_id, user_id, query_text, hit_count, created_at, last_seen_at) "
                    "VALUES (?, ?, ?, 1, ?, ?)",
                    (workspace_id, user_id, query_text, now, now),
                )
            conn.commit()
    except sqlite3.OperationalError:
        pass


def resolve_query(workspace_id: str, query_text: str) -> None:
    """Called whenever Ask Nexora successfully answers a question — if
    this exact question (case-insensitive) was previously logged as
    unresolved, clear it. Covers the "we taught it, now it works" case:
    re-asking the same question that used to fail quietly removes the
    stale troubleshoot entry instead of leaving it for manual cleanup."""
    query_text = (query_text or "").strip()
    if not query_text or not workspace_id:
        return
    try:
        with sqlite3.connect(_db_path()) as conn:
            _ensure_table(conn)
            conn.execute(
                "DELETE FROM ask_nexora_unresolved_queries "
                "WHERE workspace_id = ? AND LOWER(query_text) = LOWER(?)",
                (workspace_id, query_text),
            )
            conn.commit()
    except sqlite3.OperationalError:
        pass


@ask_nexora_troubleshoot_bp.route("", methods=["GET"])
@require_jwt_auth
def list_unresolved():
    workspace_id = get_workspace_id()
    with sqlite3.connect(_db_path()) as conn:
        _ensure_table(conn)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM ask_nexora_unresolved_queries "
            "WHERE workspace_id = ? ORDER BY last_seen_at DESC",
            (workspace_id,),
        ).fetchall()
    items = [_row_to_dict(r) for r in rows]
    return jsonify({"success": True, "data": {"questions": items, "count": len(items)}})


@ask_nexora_troubleshoot_bp.route("/<int:entry_id>", methods=["DELETE"])
@require_jwt_auth
def delete_unresolved(entry_id: int):
    workspace_id = get_workspace_id()
    with sqlite3.connect(_db_path()) as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT id FROM ask_nexora_unresolved_queries WHERE id = ? AND workspace_id = ?",
            (entry_id, workspace_id),
        ).fetchone()
        if not row:
            return jsonify({"success": False, "error": {"message": "Entry not found"}}), 404
        conn.execute(
            "DELETE FROM ask_nexora_unresolved_queries WHERE id = ? AND workspace_id = ?",
            (entry_id, workspace_id),
        )
        conn.commit()
    return jsonify({"success": True, "data": {"deleted": True, "id": entry_id}})

"""Visible record of every automatic FO ↔ SO match decision.

Why this exists
---------------
Automation is only trustworthy when you can see what it did. Two real
incidents drove this:

1. The FO↔SO self-heal silently called a function that did not exist, so
   it failed on every run for every user and nobody could tell — the
   caller swallowed the exception (2026-08-28).
2. Auto-attach guessed a Filled Order when it could not identify the SO's
   category, merging Bath/towel lines into a Bed-only FO and destroying
   real bedsheet match data. It was only noticed by eye, days later.

Fixing (2) means auto-attach now REFUSES to guess — which introduces its
own silent failure: an SO that legitimately cannot be placed just never
appears, with no explanation. So every decision is recorded here,
especially the refusals: `needs_attention` rows are the ones asking a
human to finish the job.

Mirrors `gmail_import_log` (see centralized_db_system/db.py), which does
the same for mail imports and is surfaced as "View import history".
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

# Automation completed the work; nothing is being asked of the user.
OUTCOME_MATCHED_NEW = "matched_new"
OUTCOME_MATCHED_UPDATED = "matched_updated"
OUTCOME_ALREADY_MATCHED = "already_matched"

# Automation deliberately stopped. These need a human to place the SO —
# each one is an SO that will NOT show up under any Filled Order until
# someone acts on it.
OUTCOME_SKIPPED_NO_FO = "skipped_no_fo"
OUTCOME_SKIPPED_CATEGORY_MISMATCH = "skipped_category_mismatch"
OUTCOME_SKIPPED_UNREADABLE = "skipped_unreadable"
OUTCOME_ERROR = "error"

NEEDS_ATTENTION_OUTCOMES = frozenset({
    OUTCOME_SKIPPED_NO_FO,
    OUTCOME_SKIPPED_CATEGORY_MISMATCH,
    OUTCOME_SKIPPED_UNREADABLE,
    OUTCOME_ERROR,
})

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fo_so_auto_match_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    workspace_id TEXT,
    created_at TEXT NOT NULL,
    source TEXT,
    outcome TEXT NOT NULL,
    needs_attention INTEGER NOT NULL DEFAULT 0,
    detail TEXT,
    distributor_id INTEGER,
    tracking_id INTEGER,
    so_numbers TEXT,
    so_category TEXT,
    filled_order_id INTEGER,
    fo_category TEXT,
    fo_season TEXT,
    run_id INTEGER,
    archive_ids TEXT
);

CREATE INDEX IF NOT EXISTS idx_fo_so_auto_match_log_user
    ON fo_so_auto_match_log(user_id, workspace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_fo_so_auto_match_log_attention
    ON fo_so_auto_match_log(user_id, needs_attention, created_at);
"""

_COLUMNS = [
    "user_id", "workspace_id", "created_at", "source", "outcome",
    "needs_attention", "detail", "distributor_id", "tracking_id",
    "so_numbers", "so_category", "filled_order_id", "fo_category",
    "fo_season", "run_id", "archive_ids",
]


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_text_list(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value if v not in (None, "")]
        return ", ".join(items) if items else None
    text = str(value).strip()
    return text or None


def record(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    outcome: str,
    workspace_id: str | None = None,
    source: str | None = None,
    detail: str | None = None,
    distributor_id: int | None = None,
    tracking_id: int | None = None,
    so_numbers: Any = None,
    so_category: Any = None,
    filled_order_id: int | None = None,
    fo_category: str | None = None,
    fo_season: str | None = None,
    run_id: int | None = None,
    archive_ids: list[int] | None = None,
) -> int | None:
    """Record one auto-match decision. Never raises — logging must not be
    able to break the match it is describing."""
    try:
        ensure_schema(conn)
        cursor = conn.execute(
            f"INSERT INTO fo_so_auto_match_log ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
            (
                int(user_id),
                str(workspace_id or "default"),
                _now(),
                source,
                outcome,
                1 if outcome in NEEDS_ATTENTION_OUTCOMES else 0,
                detail,
                distributor_id,
                tracking_id,
                _as_text_list(so_numbers),
                _as_text_list(so_category),
                filled_order_id,
                fo_category,
                fo_season,
                run_id,
                json.dumps(archive_ids) if archive_ids else None,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    except Exception:
        return None


def list_decisions(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    workspace_id: str | None = None,
    needs_attention_only: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Most recent decisions first, with the distributor's readable name
    joined in so the caller doesn't need a second lookup."""
    ensure_schema(conn)
    # The distributor name is a nicety; the log itself is the point. If
    # master_distributors isn't present, still return the entries rather
    # than failing the whole view — this record exists precisely for the
    # moments when something else is already broken.
    has_distributors = bool(
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='master_distributors'"
        ).fetchone()
    )
    name_select = (
        "COALESCE(md.firm_name, md.name, 'Unknown') AS distributor_name"
        if has_distributors
        else "NULL AS distributor_name"
    )
    join_clause = (
        " LEFT JOIN master_distributors md ON l.distributor_id = md.id"
        if has_distributors
        else ""
    )
    sql = (
        "SELECT l.id, l.created_at, l.source, l.outcome, l.needs_attention, "
        "l.detail, l.distributor_id, "
        f"{name_select}, "
        "l.tracking_id, l.so_numbers, l.so_category, l.filled_order_id, "
        "l.fo_category, l.fo_season, l.run_id, l.archive_ids "
        "FROM fo_so_auto_match_log l"
        f"{join_clause}"
        " WHERE l.user_id = ?"
    )
    params: list[Any] = [int(user_id)]
    if workspace_id:
        sql += " AND l.workspace_id = ?"
        params.append(str(workspace_id))
    if needs_attention_only:
        sql += " AND l.needs_attention = 1"
    sql += " ORDER BY l.id DESC LIMIT ?"
    params.append(max(1, int(limit)))

    previous_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.row_factory = previous_factory

    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["needs_attention"] = bool(item.get("needs_attention"))
        raw_ids = item.pop("archive_ids", None)
        try:
            item["archive_ids"] = json.loads(raw_ids) if raw_ids else []
        except (TypeError, ValueError):
            item["archive_ids"] = []
        out.append(item)
    return out


def count_needs_attention(
    conn: sqlite3.Connection, *, user_id: int, workspace_id: str | None = None
) -> int:
    """How many SOs automation could not place on its own — the number
    worth showing as a badge so these never sit unnoticed."""
    ensure_schema(conn)
    sql = (
        "SELECT COUNT(*) FROM fo_so_auto_match_log "
        "WHERE user_id = ? AND needs_attention = 1"
    )
    params: list[Any] = [int(user_id)]
    if workspace_id:
        sql += " AND workspace_id = ?"
        params.append(str(workspace_id))
    row = conn.execute(sql, tuple(params)).fetchone()
    return int(row[0]) if row else 0

"""Self-diagnosing Gmail CI/SO polls — why an inbox produced nothing.

Same pattern (and the same rules) as `app/services/so_pack_diagnostics.py`:
outcome codes that double as the app's i18n keys, one row per unhealthy run
written against `user_id`, and the workspace owner reading workspace-wide
through the existing owner-global exception. Only the columns differ, because a
mail poll describes an inbox scan rather than one uploaded container.

Why this exists: the poller used to answer "0 CI, 0 SO" for four different
reasons — mailbox not connected, no mail matched the search, mail matched but
every attachment was unreadable, or attachments read fine but nothing looked
like a Sales Order. The user could not tell those apart, and neither could we
without asking him to forward the mail.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

# Outcome codes — shared verbatim with the Android string catalogs.
IMPORTED = "mail_sync_imported"
NOT_CONNECTED = "mail_sync_not_connected"
NO_MAIL = "mail_sync_no_matching_mail"
NOTHING_NEW = "mail_sync_nothing_new"
UNREADABLE = "mail_sync_attachments_unreadable"
NOT_RECOGNISED = "mail_sync_not_recognised"
NEEDS_REVIEW = "mail_sync_needs_review"

# Outcomes worth keeping evidence for. A clean import writes nothing.
_HEALTHY = (IMPORTED,)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mail_sync_poll_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    workspace_id TEXT,
    created_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    query TEXT,
    messages_matched INTEGER DEFAULT 0,
    messages_new INTEGER DEFAULT 0,
    attachments_seen INTEGER DEFAULT 0,
    attachments_unreadable INTEGER DEFAULT 0,
    ci_imported INTEGER DEFAULT 0,
    so_imported INTEGER DEFAULT 0,
    pending_review INTEGER DEFAULT 0,
    duplicates INTEGER DEFAULT 0,
    report_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mail_sync_diag_user
    ON mail_sync_poll_diagnostics(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_mail_sync_diag_outcome
    ON mail_sync_poll_diagnostics(outcome, created_at);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def assess(summary: dict[str, Any]) -> dict[str, Any]:
    """Classify one poll run into an outcome the user can act on."""
    s = summary or {}
    matched = int(s.get("messages_matched") or 0)
    scanned = int(s.get("scanned") or 0)
    imported = int(s.get("ci_imported") or 0) + int(s.get("so_staged") or 0)
    pending = int(s.get("pending_review") or 0)
    duplicates = int(s.get("duplicates") or 0)
    seen = int(s.get("attachments_seen") or 0)
    unreadable = int(s.get("attachments_unreadable") or 0)

    if s.get("connected") is False:
        outcome = NOT_CONNECTED
    elif imported:
        outcome = IMPORTED
    elif pending:
        outcome = NEEDS_REVIEW
    elif matched == 0:
        outcome = NO_MAIL
    elif scanned == 0:
        # Everything the search matched had already been processed before.
        outcome = NOTHING_NEW if not duplicates else NOTHING_NEW
    elif seen and unreadable >= seen:
        outcome = UNREADABLE
    elif duplicates:
        outcome = NOTHING_NEW
    else:
        outcome = NOT_RECOGNISED

    return {
        "outcome": outcome,
        "params": {
            "messages_matched": matched,
            "messages_new": scanned,
            "attachments_seen": seen,
            "attachments_unreadable": unreadable,
            "ci_imported": int(s.get("ci_imported") or 0),
            "so_imported": int(s.get("so_staged") or 0),
            "pending_review": pending,
            "duplicates": duplicates,
            "window_days": int(s.get("window_days") or 0),
        },
        "unreadable_files": list(s.get("unreadable_files") or [])[:20],
        "skipped_reasons": list(s.get("skipped_reasons") or [])[:20],
    }


def message_for(assessment: dict[str, Any]) -> str:
    """Plain-language fallback text (the app prefers its own translation)."""
    p = assessment.get("params") or {}
    outcome = assessment.get("outcome")
    if outcome == NOT_CONNECTED:
        return (
            "Your mailbox is not connected, so no sales orders can be pulled from "
            "email. Connect Gmail in Settings → Mail sync."
        )
    if outcome == NO_MAIL:
        return (
            f"Mailbox connected. No sales order or invoice email arrived in the last "
            f"{p.get('window_days', 0)} days, so there was nothing to import."
        )
    if outcome == NOTHING_NEW:
        return (
            f"Mailbox connected. All {p.get('messages_matched', 0)} matching email(s) "
            "had already been imported earlier — nothing new."
        )
    if outcome == UNREADABLE:
        files = ", ".join(
            str(f.get("filename")) for f in (assessment.get("unreadable_files") or [])[:8]
        )
        return (
            f"Found {p.get('messages_new', 0)} email(s), but the attachment(s) could "
            "not be read"
            + (f": {files}" if files else "")
            + ". Please send the original PDF or ZIP, not a scan or a photo."
        )
    if outcome == NOT_RECOGNISED:
        return (
            f"Found {p.get('messages_new', 0)} email(s) with "
            f"{p.get('attachments_seen', 0)} attachment(s), but none of them looked "
            "like a Sales Order or Commercial Invoice, so nothing was imported."
        )
    if outcome == NEEDS_REVIEW:
        return (
            f"{p.get('pending_review', 0)} document(s) came in from email but need you "
            "to pick the distributor before they can be saved."
        )
    return (
        f"{p.get('so_imported', 0)} sales order(s) and {p.get('ci_imported', 0)} "
        "invoice(s) imported from email."
    )


def record(
    conn: sqlite3.Connection,
    *,
    user_id: int | None,
    workspace_id: str | None,
    query: str | None,
    summary: dict[str, Any],
    assessment: dict[str, Any],
) -> int | None:
    """Persist one unhealthy poll. A clean import writes nothing at all."""
    if user_id is None:
        return None
    if assessment.get("outcome") in _HEALTHY:
        return None
    p = assessment.get("params") or {}
    try:
        ensure_schema(conn)
        cur = conn.execute(
            "INSERT INTO mail_sync_poll_diagnostics ("
            "user_id, workspace_id, created_at, outcome, query, messages_matched, "
            "messages_new, attachments_seen, attachments_unreadable, ci_imported, "
            "so_imported, pending_review, duplicates, report_json"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(user_id),
                workspace_id,
                _now(),
                str(assessment.get("outcome")),
                str(query or "")[:2000],
                int(p.get("messages_matched") or 0),
                int(p.get("messages_new") or 0),
                int(p.get("attachments_seen") or 0),
                int(p.get("attachments_unreadable") or 0),
                int(p.get("ci_imported") or 0),
                int(p.get("so_imported") or 0),
                int(p.get("pending_review") or 0),
                int(p.get("duplicates") or 0),
                json.dumps(
                    {
                        "assessment": assessment,
                        "debug": (summary or {}).get("debug") or [],
                        "errors": (summary or {}).get("errors") or [],
                        "unreadable_files": (summary or {}).get("unreadable_files") or [],
                        "skipped_reasons": (summary or {}).get("skipped_reasons") or [],
                    },
                    default=str,
                )[:400000],
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    except Exception:
        # Diagnostics must never break the poll they are describing.
        return None


def list_recent(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    workspace_wide: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Own records; the workspace owner may read the whole workspace."""
    ensure_schema(conn)
    sql = (
        "SELECT id, user_id, created_at, outcome, query, messages_matched, "
        "messages_new, attachments_seen, attachments_unreadable, ci_imported, "
        "so_imported, pending_review, duplicates, report_json "
        "FROM mail_sync_poll_diagnostics "
    )
    params: list[Any] = []
    if not workspace_wide:
        sql += "WHERE user_id = ? "
        params.append(int(user_id))
    sql += "ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    out: list[dict[str, Any]] = []
    for row in conn.execute(sql, params).fetchall():
        item = {
            "id": row[0],
            "user_id": row[1],
            "created_at": row[2],
            "outcome": row[3],
            "query": row[4],
            "messages_matched": row[5],
            "messages_new": row[6],
            "attachments_seen": row[7],
            "attachments_unreadable": row[8],
            "ci_imported": row[9],
            "so_imported": row[10],
            "pending_review": row[11],
            "duplicates": row[12],
        }
        try:
            item["report"] = json.loads(row[13]) if row[13] else None
        except ValueError:
            item["report"] = None
        out.append(item)
    return out

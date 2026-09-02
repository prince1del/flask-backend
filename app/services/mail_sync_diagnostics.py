"""Self-diagnosing Gmail CI polls — why an inbox produced nothing.

Outcome codes double as the Android i18n keys (BdUiCatalog.MailSync.forCode).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

IMPORTED = "mail_sync_imported"
NOT_CONNECTED = "mail_sync_not_connected"
NO_MAIL = "mail_sync_no_matching_mail"
NOTHING_NEW = "mail_sync_nothing_new"
UNREADABLE = "mail_sync_attachments_unreadable"
NOT_RECOGNISED = "mail_sync_not_recognised"
NEEDS_REVIEW = "mail_sync_needs_review"

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
        outcome = NOTHING_NEW
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
    p = assessment.get("params") or {}
    outcome = assessment.get("outcome")
    if outcome == NOT_CONNECTED:
        return (
            "Your mailbox is not connected. Connect Gmail in Settings → Mail sync."
        )
    if outcome == NO_MAIL:
        return (
            f"Mailbox connected. No invoice email arrived in the last "
            f"{p.get('window_days', 0)} days."
        )
    if outcome == NOTHING_NEW:
        return "Mailbox connected. All matching emails were already processed."
    if outcome == UNREADABLE:
        return "Found emails, but attachments could not be read (scan/photo PDF?)."
    if outcome == NOT_RECOGNISED:
        return "Found emails with attachments, but none looked like a Commercial Invoice."
    if outcome == NEEDS_REVIEW:
        return (
            f"{p.get('pending_review', 0)} invoice(s) need you to pick the distributor."
        )
    return f"{p.get('ci_imported', 0)} commercial invoice(s) imported from email."


def record(
    conn: sqlite3.Connection,
    *,
    user_id: int | None,
    workspace_id: str | None,
    query: str | None,
    summary: dict[str, Any],
    assessment: dict[str, Any],
) -> int | None:
    if user_id is None or assessment.get("outcome") in _HEALTHY:
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
        return None

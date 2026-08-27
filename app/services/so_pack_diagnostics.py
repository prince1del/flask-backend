"""Self-diagnosing SO pack uploads — why a pack read badly, without the file.

The Bernina AW26 incident was only debuggable because that user still had his
ZIP and could hand it over. Any other BD user hitting the same thing would just
see "SO qty 0 / MISSING ON SO" and we would have nothing to look at.

So every upload that ends with no usable Sales Order lines, or with only some of
its Sales Orders readable, now leaves a record behind:

* what was uploaded (name, size, container type, inner entries),
* per inner file: pages, image count, extracted text length, SO number found,
  lines parsed, and the reason it was rejected (`no_text_layer` for a scanned or
  photographed PDF, `pdf_unreadable`, `empty_pdf`, `layout_not_recognised`),
* which Sales Orders parsed and which did not,
* the outcome code the user was shown.

Rows are written per `user_id` exactly like every other business record; the
workspace owner reads workspace-wide through the same owner-global rule used by
the Order Match auto-heal. The source file itself is parked in the existing
Order Desk recycle area under the same 90-day retention.

The outcome codes are also the i18n keys the app renders, so the user gets plain
language ("this looks like a scanned PDF…") instead of an empty match.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

# Outcome codes — shared with the Android string catalogs.
OK = "so_pack_ok"
NO_FILES = "so_pack_no_files"
SCANNED = "so_pack_scanned_pdfs"
UNREADABLE = "so_pack_unreadable"
PARTIAL = "so_pack_partial"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS so_pack_upload_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    workspace_id TEXT,
    created_at TEXT NOT NULL,
    source_filename TEXT,
    source_bytes INTEGER,
    container TEXT,
    outcome TEXT NOT NULL,
    files_total INTEGER DEFAULT 0,
    files_parsed INTEGER DEFAULT 0,
    so_parsed INTEGER DEFAULT 0,
    lines_parsed INTEGER DEFAULT 0,
    total_qty REAL DEFAULT 0,
    so_numbers_ok TEXT,
    so_numbers_failed TEXT,
    report_json TEXT NOT NULL,
    kept_file_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_so_pack_diag_user
    ON so_pack_upload_diagnostics(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_so_pack_diag_outcome
    ON so_pack_upload_diagnostics(outcome, created_at);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _reports(pack_meta: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in (pack_meta.get("file_reports") or []) if isinstance(r, dict)]


def assess(
    pack_meta: dict[str, Any] | None,
    *,
    usable_lines: int,
    contents: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify one parsed pack into an outcome the user can act on.

    Returns {"outcome", "params", "ok_so_numbers", "failed", ...}. `params` are
    the numbers the app substitutes into the translated message.
    """
    meta = pack_meta or {}
    reports = _reports(meta)
    ok = [r for r in reports if r.get("lines")]
    bad = [r for r in reports if not r.get("lines")]
    scanned = [r for r in bad if r.get("reason") == "no_text_layer"]
    ok_numbers = sorted({str(r.get("so_number")) for r in ok if r.get("so_number")})
    failed = [
        {
            "file": r.get("source_pdf"),
            "so_number": r.get("so_number"),
            "reason": r.get("reason"),
        }
        for r in bad
    ]

    if usable_lines <= 0:
        if not reports:
            # Nothing even looked like a Sales Order document. Only claim "no
            # files" when the container was actually inspected — on the match
            # route we hold the parsed JSON alone and must not guess.
            outcome = (
                NO_FILES
                if contents is not None and not contents.get("pdf_entries")
                else UNREADABLE
            )
        elif scanned and len(scanned) == len(bad):
            outcome = SCANNED
        else:
            outcome = UNREADABLE
    elif bad:
        outcome = PARTIAL
    else:
        outcome = OK

    return {
        "outcome": outcome,
        "params": {
            "files_total": int(meta.get("files_total") or len(reports)),
            "files_parsed": len(ok),
            "files_failed": len(bad),
            "so_parsed": len(ok_numbers),
            "scanned_files": len(scanned),
        },
        "ok_so_numbers": ok_numbers,
        "failed": failed,
        "usable_lines": int(usable_lines),
    }


def message_for(assessment: dict[str, Any]) -> str:
    """Plain-language fallback text (the app prefers its own translation)."""
    p = assessment.get("params") or {}
    outcome = assessment.get("outcome")
    if outcome == NO_FILES:
        return (
            "This upload contained no sales order files. Send the ZIP that holds "
            "the sales order PDFs, or the PDFs themselves."
        )
    if outcome == SCANNED:
        return (
            "The sales order lines could not be read — these look like scanned or "
            "photographed PDFs, so there is no text to read. Please share the "
            "original PDF from your email instead of a scan or screenshot."
        )
    if outcome == UNREADABLE:
        return (
            "No sales order lines could be read from this pack. Nothing has been "
            "attached to this Filled Order. We have saved the details needed to "
            "look into it — no need to send us the file."
        )
    if outcome == PARTIAL:
        failed_list = ", ".join(
            str(f.get("so_number") or f.get("file"))
            for f in (assessment.get("failed") or [])[:12]
        )
        return (
            f"{p.get('so_parsed', 0)} sales order(s) were read, but "
            f"{p.get('files_failed', 0)} could not be read"
            + (f": {failed_list}" if failed_list else "")
            + ". Only the readable ones have been attached."
        )
    return ""


def record(
    conn: sqlite3.Connection,
    *,
    user_id: int | None,
    workspace_id: str | None,
    source_filename: str | None,
    source_bytes: int | None,
    pack_meta: dict[str, Any] | None,
    assessment: dict[str, Any],
    contents: dict[str, Any] | None = None,
    kept_file_path: str | None = None,
) -> int | None:
    """Persist one unhealthy upload. Healthy packs write nothing at all."""
    if user_id is None:
        return None
    if assessment.get("outcome") == OK:
        return None
    meta = pack_meta or {}
    reports = _reports(meta)
    try:
        ensure_schema(conn)
        cur = conn.execute(
            "INSERT INTO so_pack_upload_diagnostics ("
            "user_id, workspace_id, created_at, source_filename, source_bytes, "
            "container, outcome, files_total, files_parsed, so_parsed, "
            "lines_parsed, total_qty, so_numbers_ok, so_numbers_failed, "
            "report_json, kept_file_path"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(user_id),
                workspace_id,
                _now(),
                str(source_filename or "")[:255],
                int(source_bytes or 0),
                str((contents or {}).get("container") or "")[:32],
                str(assessment.get("outcome")),
                int((assessment.get("params") or {}).get("files_total") or 0),
                int((assessment.get("params") or {}).get("files_parsed") or 0),
                int((assessment.get("params") or {}).get("so_parsed") or 0),
                int(meta.get("line_rows") or 0),
                float(meta.get("total_qty") or 0),
                json.dumps(assessment.get("ok_so_numbers") or []),
                json.dumps(assessment.get("failed") or [], default=str),
                json.dumps(
                    {
                        "assessment": assessment,
                        "pack_meta": {
                            k: v for k, v in meta.items() if k != "file_reports"
                        },
                        "file_reports": reports,
                        "contents": contents or {},
                    },
                    default=str,
                )[:400000],
                kept_file_path,
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    except Exception:
        # Diagnostics must never break the upload they are describing.
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
        "SELECT id, user_id, created_at, source_filename, source_bytes, container, "
        "outcome, files_total, files_parsed, so_parsed, lines_parsed, total_qty, "
        "so_numbers_ok, so_numbers_failed, report_json, kept_file_path "
        "FROM so_pack_upload_diagnostics "
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
            "source_filename": row[3],
            "source_bytes": row[4],
            "container": row[5],
            "outcome": row[6],
            "files_total": row[7],
            "files_parsed": row[8],
            "so_parsed": row[9],
            "lines_parsed": row[10],
            "total_qty": row[11],
            "kept_file_path": row[15],
        }
        for key, raw in (
            ("so_numbers_ok", row[12]),
            ("so_numbers_failed", row[13]),
            ("report", row[14]),
        ):
            try:
                item[key] = json.loads(raw) if raw else None
            except ValueError:
                item[key] = None
        out.append(item)
    return out

"""Order Desk recycle store — snapshot on delete, restore on re-upload.

See docs/ORDER_DESK_RECYCLE.md for product rules.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.services import fo_so_match_db as matchdb

RETENTION_DAYS = 90
PURGE_THROTTLE_SECONDS = 6 * 3600
_last_purge_at: float = 0.0

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS order_desk_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    restore_scope TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    filled_order_id INTEGER,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    restored_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_order_desk_archive_user_kind_key
    ON order_desk_archive(user_id, kind, entity_key);
CREATE INDEX IF NOT EXISTS idx_order_desk_archive_expires
    ON order_desk_archive(expires_at);
CREATE INDEX IF NOT EXISTS idx_order_desk_archive_fo
    ON order_desk_archive(user_id, filled_order_id);
"""

_schema_ensured = False


def ensure_schema(conn: sqlite3.Connection) -> None:
    global _schema_ensured
    if _schema_ensured:
        try:
            conn.execute("SELECT 1 FROM order_desk_archive LIMIT 1")
            return
        except sqlite3.OperationalError:
            _schema_ensured = False
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    _schema_ensured = True


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _expires_at() -> str:
    return (
        datetime.now(timezone.utc) + timedelta(days=RETENTION_DAYS)
    ).strftime("%Y-%m-%d %H:%M:%S")


def _insert_archive(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    kind: str,
    entity_key: str,
    restore_scope: str,
    payload: dict[str, Any],
    filled_order_id: int | None = None,
) -> None:
    ensure_schema(conn)
    conn.execute(
        """
        INSERT INTO order_desk_archive (
            user_id, kind, entity_key, restore_scope, payload_json,
            filled_order_id, created_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            kind,
            entity_key,
            restore_scope,
            json.dumps(payload, default=str),
            filled_order_id,
            _now(),
            _expires_at(),
        ),
    )


def fo_entity_key(
    distributor_name: str | None,
    category: str | None,
    season: str | None,
) -> str:
    parts = [
        (distributor_name or "").strip(),
        (category or "").strip(),
        (season or "").strip(),
    ]
    return "|".join(parts)


def upload_root() -> Path:
    if Path("app/instance").exists():
        return (Path("app/instance/order_fulfillment_files")).resolve()
    return (Path("instance/order_fulfillment_files")).resolve()


def recycle_root(user_id: int) -> Path:
    return upload_root() / "_nexora_recycle" / str(user_id)


def move_file_to_recycle(user_id: int, file_ref: str | None) -> str | None:
    """Move an upload-root file into _nexora_recycle; return relative recycle path."""
    if not file_ref or not str(file_ref).strip():
        return None
    try:
        src = Path(file_ref).resolve()
        root = upload_root()
        src.relative_to(root)
    except (ValueError, OSError):
        return None
    if not src.is_file():
        return None
    dest_dir = recycle_root(user_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        n = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{n}{suffix}"
            n += 1
    try:
        shutil.move(str(src), str(dest))
    except OSError:
        return None
    try:
        return str(dest.relative_to(root))
    except ValueError:
        return str(dest)


# ---------------------------------------------------------------------------
# Archive writers
# ---------------------------------------------------------------------------


def archive_match_run(
    conn: sqlite3.Connection,
    user_id: int,
    run: dict[str, Any],
    *,
    restore_scope: str = "run",
) -> None:
    run_id = int(run.get("id") or 0)
    if not run_id:
        return
    payload = {
        "run": run,
        "so_numbers": matchdb.extract_so_numbers_from_run_row(run),
    }
    _insert_archive(
        conn,
        user_id=user_id,
        kind="match_run",
        entity_key=f"run:{run_id}",
        restore_scope=restore_scope,
        payload=payload,
        filled_order_id=int(run["filled_order_id"]) if run.get("filled_order_id") else None,
    )


def archive_match_so(
    conn: sqlite3.Connection,
    user_id: int,
    run: dict[str, Any],
    so_number: str,
    *,
    restore_scope: str = "entity",
) -> None:
    key = matchdb.normalize_so_number(so_number)
    if not key:
        return
    want = key.upper()
    lines = [
        dict(l)
        for l in (run.get("so_line_detail") or [])
        if isinstance(l, dict)
        and (matchdb.normalize_so_number(l.get("so_number")) or "").upper() == want
    ]
    rows: list[dict[str, Any]] = []
    for r in run.get("rows") or []:
        if not isinstance(r, dict):
            continue
        breakdown = r.get("so_breakdown") or []
        if not isinstance(breakdown, list):
            continue
        if any(
            isinstance(c, dict)
            and (matchdb.normalize_so_number(c.get("so_number")) or "").upper() == want
            for c in breakdown
        ):
            rows.append(dict(r))
    so_totals = run.get("so_totals") if isinstance(run.get("so_totals"), dict) else {}
    payload = {
        "run_id": run.get("id"),
        "filled_order_id": run.get("filled_order_id"),
        "so_number": key,
        "so_line_detail": lines,
        "rows": rows,
        "so_totals": {k: v for k, v in so_totals.items() if str(k).upper() == want.upper()},
        "run_meta": {
            k: run.get(k)
            for k in (
                "distributor_id",
                "distributor_name",
                "category",
                "season",
                "fo_source_filename",
                "so_buyer_label",
                "so_source_filename",
            )
        },
    }
    _insert_archive(
        conn,
        user_id=user_id,
        kind="match_so",
        entity_key=key,
        restore_scope=restore_scope,
        payload=payload,
        filled_order_id=int(run["filled_order_id"]) if run.get("filled_order_id") else None,
    )


def archive_tracking_bundle(
    conn: sqlite3.Connection,
    user_id: int,
    tracking: dict[str, Any],
    *,
    fulfillment_items: list[dict[str, Any]],
    achievements: list[dict[str, Any]],
    payment_entries: list[dict[str, Any]],
    processed_documents: list[dict[str, Any]],
    restore_scope: str = "run",
) -> None:
    order_ref = str(tracking.get("order_ref_no") or "").strip()
    if not order_ref:
        return
    payload = {
        "tracking": tracking,
        "fulfillment_items": fulfillment_items,
        "achievements": achievements,
        "payment_entries": payment_entries,
        "processed_documents": processed_documents,
    }
    _insert_archive(
        conn,
        user_id=user_id,
        kind="tracking",
        entity_key=order_ref,
        restore_scope=restore_scope,
        payload=payload,
        filled_order_id=None,
    )


def archive_filled_order(
    conn: sqlite3.Connection,
    user_id: int,
    order: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    restore_scope: str = "run",
) -> None:
    key = fo_entity_key(
        order.get("distributor_name_raw"),
        order.get("category"),
        order.get("season"),
    )
    payload = {"order": order, "items": items}
    _insert_archive(
        conn,
        user_id=user_id,
        kind="filled_order",
        entity_key=key,
        restore_scope=restore_scope,
        payload=payload,
        filled_order_id=int(order["id"]) if order.get("id") else None,
    )


def archive_file_reference(
    conn: sqlite3.Connection,
    user_id: int,
    recycle_rel_path: str,
    *,
    restore_scope: str = "run",
    tracking_id: int | None = None,
    kind_hint: str | None = None,
) -> None:
    if not recycle_rel_path:
        return
    payload = {
        "recycle_path": recycle_rel_path,
        "tracking_id": tracking_id,
        "kind_hint": kind_hint,
    }
    _insert_archive(
        conn,
        user_id=user_id,
        kind="file",
        entity_key=recycle_rel_path,
        restore_scope=restore_scope,
        payload=payload,
    )


def collect_tracking_bundle(
    conn: sqlite3.Connection,
    tracking_id: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    conn.row_factory = sqlite3.Row
    items = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM order_fulfillment_items WHERE order_lifecycle_id = ?",
            (tracking_id,),
        ).fetchall()
    ]
    achievements = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM achievements WHERE order_lifecycle_tracking_id = ?",
            (tracking_id,),
        ).fetchall()
    ]
    payments = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM distributor_payment_entries WHERE tracking_id = ?",
            (tracking_id,),
        ).fetchall()
    ]
    processed = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM processed_documents WHERE tracking_id = ?",
            (tracking_id,),
        ).fetchall()
    ]
    return items, achievements, payments, processed


# ---------------------------------------------------------------------------
# Restore helpers
# ---------------------------------------------------------------------------


def _pending_archives(
    conn: sqlite3.Connection,
    user_id: int,
    kind: str,
    entity_key: str | None = None,
    filled_order_id: int | None = None,
) -> list[dict[str, Any]]:
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    sql = (
        "SELECT id, kind, entity_key, restore_scope, payload_json, filled_order_id "
        "FROM order_desk_archive "
        "WHERE user_id = ? AND kind = ? AND restored_at IS NULL "
        "AND datetime(expires_at) > datetime('now')"
    )
    params: list[Any] = [user_id, kind]
    if entity_key is not None:
        sql += " AND entity_key = ?"
        params.append(entity_key)
    if filled_order_id is not None:
        sql += " AND (filled_order_id IS NULL OR filled_order_id = ?)"
        params.append(filled_order_id)
    sql += " ORDER BY id ASC"
    rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        out.append(
            {
                "id": int(row["id"]),
                "kind": row["kind"],
                "entity_key": row["entity_key"],
                "restore_scope": row["restore_scope"],
                "payload": payload,
                "filled_order_id": row["filled_order_id"],
            }
        )
    return out


def _mark_restored(conn: sqlite3.Connection, archive_id: int) -> None:
    conn.execute(
        "UPDATE order_desk_archive SET restored_at = ? WHERE id = ?",
        (_now(), archive_id),
    )


def _so_claimed_by_other_run(
    conn: sqlite3.Connection,
    so_number: str,
    *,
    except_run_id: int | None,
) -> bool:
    matchdb.ensure_schema(conn)
    key = matchdb.normalize_so_number(so_number)
    if not key:
        return False
    row = conn.execute(
        "SELECT run_id FROM fo_so_match_so_index WHERE UPPER(so_number) = UPPER(?)",
        (key,),
    ).fetchone()
    if not row:
        return False
    rid = int(row[0])
    return except_run_id is None or rid != int(except_run_id)


def restore_match_archives_after_save(
    conn: sqlite3.Connection,
    user_id: int,
    run_id: int,
    filled_order_id: int | None,
    uploaded_so_numbers: list[str],
) -> int:
    """Merge archived match_so rows into a freshly saved run. Returns restore count."""
    if not uploaded_so_numbers:
        return 0
    run = matchdb.get_match_run(conn, run_id, user_id=user_id)
    if not run:
        return 0
    restored = 0
    uploaded_upper = {
        (matchdb.normalize_so_number(n) or "").upper()
        for n in uploaded_so_numbers
        if matchdb.normalize_so_number(n)
    }
    for so_key in sorted(uploaded_upper):
        if _so_claimed_by_other_run(conn, so_key, except_run_id=run_id):
            continue
        archives = _pending_archives(conn, user_id, "match_so", entity_key=so_key, filled_order_id=filled_order_id)
        if not archives:
            continue
        archive_row = archives[-1]
        payload = archive_row["payload"]
        if filled_order_id and payload.get("filled_order_id"):
            if int(payload["filled_order_id"]) != int(filled_order_id):
                continue
        merged = _merge_archived_so_into_run(run, payload)
        if merged is None:
            continue
        run = merged
        _mark_restored(conn, archive_row["id"])
        restored += 1
    if restored:
        _persist_run(conn, run_id, user_id, run)
        conn.commit()
    return restored


def _merge_archived_so_into_run(
    run: dict[str, Any],
    archive_payload: dict[str, Any],
) -> dict[str, Any] | None:
    so_number = matchdb.normalize_so_number(archive_payload.get("so_number"))
    if not so_number:
        return None
    want = so_number.upper()
    existing_nums = {
        (matchdb.normalize_so_number(n) or "").upper()
        for n in matchdb.extract_so_numbers_from_run_row(run)
    }
    # Newer wins: if this SO is already fully present in the new run, skip merge.
    if want in existing_nums:
        archived_lines = archive_payload.get("so_line_detail") or []
        current_lines = [
            l
            for l in (run.get("so_line_detail") or [])
            if isinstance(l, dict)
            and (matchdb.normalize_so_number(l.get("so_number")) or "").upper() == want
        ]
        if len(current_lines) >= len(archived_lines):
            return run  # idempotent — newer upload already has this SO

    line_detail = list(run.get("so_line_detail") or [])
    line_detail = [
        l
        for l in line_detail
        if not (
            isinstance(l, dict)
            and (matchdb.normalize_so_number(l.get("so_number")) or "").upper() == want
        )
    ]
    for l in archive_payload.get("so_line_detail") or []:
        if isinstance(l, dict):
            line_detail.append(dict(l))

    rows = list(run.get("rows") or [])
    rows = [
        r
        for r in rows
        if not (
            isinstance(r, dict)
            and any(
                isinstance(c, dict)
                and (matchdb.normalize_so_number(c.get("so_number")) or "").upper() == want
                for c in (r.get("so_breakdown") or [])
            )
        )
    ]
    for r in archive_payload.get("rows") or []:
        if isinstance(r, dict):
            rows.append(dict(r))

    run = dict(run)
    run["so_line_detail"] = line_detail
    run["rows"] = rows
    run["so_totals"] = matchdb._compute_so_totals_from_rows(rows)
    totals = _recompute_run_header_totals(rows)
    for k, v in totals.items():
        run[k] = v
    return run


def _recompute_run_header_totals(rows: list[Any]) -> dict[str, Any]:
    fo_qty = so_qty = fo_ex = so_net = 0.0
    match_count = fuzzy_count = mismatch_count = missing_count = extra_count = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        fo_qty += float(r.get("fo_qty") or 0)
        so_qty += float(r.get("so_qty") or 0)
        fo_ex += float(r.get("fo_exmill_value") or 0)
        so_net += float(r.get("so_net_amount") or 0)
        status = str(r.get("status") or "").upper()
        if status == "MATCH":
            match_count += 1
        elif status == "MATCH_FUZZY_BRAND":
            fuzzy_count += 1
        elif status in ("QTY_MISMATCH", "VALUE_MISMATCH"):
            mismatch_count += 1
        elif status == "MISSING_ON_SO":
            missing_count += 1
        elif status == "EXTRA_ON_SO":
            extra_count += 1
    return {
        "fo_qty": round(fo_qty, 4),
        "so_qty": round(so_qty, 4),
        "delta_qty": round(fo_qty - so_qty, 4),
        "fo_exmill_value": round(fo_ex, 2),
        "so_net_amount": round(so_net, 2),
        "delta_value": round(fo_ex - so_net, 2),
        "match_count": match_count,
        "fuzzy_count": fuzzy_count,
        "mismatch_count": mismatch_count,
        "missing_count": missing_count,
        "extra_count": extra_count,
    }


def _persist_run(
    conn: sqlite3.Connection,
    run_id: int,
    user_id: int,
    run: dict[str, Any],
) -> None:
    matchdb.ensure_schema(conn)
    totals = _recompute_run_header_totals(run.get("rows") or [])
    conn.execute(
        """
        UPDATE fo_so_match_runs SET
            fo_qty = ?, so_qty = ?, delta_qty = ?,
            fo_exmill_value = ?, so_net_amount = ?, delta_value = ?,
            match_count = ?, fuzzy_count = ?, mismatch_count = ?,
            missing_count = ?, extra_count = ?,
            rows_json = ?, so_line_detail_json = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            totals["fo_qty"],
            totals["so_qty"],
            totals["delta_qty"],
            totals["fo_exmill_value"],
            totals["so_net_amount"],
            totals["delta_value"],
            totals["match_count"],
            totals["fuzzy_count"],
            totals["mismatch_count"],
            totals["missing_count"],
            totals["extra_count"],
            json.dumps(run.get("rows") or [], default=str),
            json.dumps(run.get("so_line_detail") or [], default=str),
            run_id,
            user_id,
        ),
    )


def restore_tracking_after_so_upload(
    conn: sqlite3.Connection,
    user_id: int,
    order_ref_no: str,
    tracking_id: int,
    workspace_id: str,
) -> bool:
    """Restore archived tracking side-data (payments, achievements) onto a new row."""
    order_ref = str(order_ref_no or "").strip()
    if not order_ref:
        return False
    archives = _pending_archives(conn, user_id, "tracking", entity_key=order_ref)
    if not archives:
        return False
    payload = archives[-1]["payload"]
    archive_id = archives[-1]["id"]

    for entry in payload.get("payment_entries") or []:
        if not isinstance(entry, dict):
            continue
        cols = [k for k in entry.keys() if k not in ("id", "tracking_id")]
        if not cols:
            continue
        vals = [entry.get(c) for c in cols] + [tracking_id]
        placeholders = ", ".join("?" for _ in cols) + ", ?"
        col_sql = ", ".join(cols) + ", tracking_id"
        try:
            conn.execute(
                f"INSERT INTO distributor_payment_entries ({col_sql}) VALUES ({placeholders})",
                vals,
            )
        except sqlite3.OperationalError:
            pass

    for ach in payload.get("achievements") or []:
        if not isinstance(ach, dict):
            continue
        cols = [k for k in ach.keys() if k not in ("id", "order_lifecycle_tracking_id")]
        if not cols:
            continue
        vals = [ach.get(c) for c in cols] + [tracking_id]
        placeholders = ", ".join("?" for _ in cols) + ", ?"
        col_sql = ", ".join(cols) + ", order_lifecycle_tracking_id"
        try:
            conn.execute(
                f"INSERT INTO achievements ({col_sql}) VALUES ({placeholders})",
                vals,
            )
        except sqlite3.OperationalError:
            pass

    _mark_restored(conn, archive_id)
    conn.commit()
    return True


def repoint_filled_order_archives(
    conn: sqlite3.Connection,
    user_id: int,
    entity_key: str,
    new_filled_order_id: int,
) -> None:
    """After FO re-upload, point match archives at the new FO id."""
    ensure_schema(conn)
    conn.execute(
        """
        UPDATE order_desk_archive
        SET filled_order_id = ?
        WHERE user_id = ? AND kind IN ('match_so', 'match_run', 'filled_order')
          AND entity_key = ? AND restored_at IS NULL
        """,
        (new_filled_order_id, user_id, entity_key),
    )


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def purge_expired(conn: sqlite3.Connection) -> int:
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, kind, payload_json FROM order_desk_archive "
        "WHERE datetime(expires_at) <= datetime('now')"
    ).fetchall()
    root = upload_root()
    deleted = 0
    for row in rows:
        if row["kind"] == "file":
            try:
                payload = json.loads(row["payload_json"] or "{}")
                rel = payload.get("recycle_path")
                if rel:
                    p = root / rel
                    if p.is_file():
                        p.unlink()
            except (TypeError, ValueError, json.JSONDecodeError, OSError):
                pass
        conn.execute("DELETE FROM order_desk_archive WHERE id = ?", (row["id"],))
        deleted += 1
    if deleted:
        conn.commit()
    return deleted


def maybe_purge(conn: sqlite3.Connection) -> None:
    global _last_purge_at
    now = time.time()
    if now - _last_purge_at < PURGE_THROTTLE_SECONDS:
        return
    _last_purge_at = now
    purge_expired(conn)

"""Order Desk delete → re-upload restore (match, tracking, FO, files)."""

from __future__ import annotations

import json
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
    so_numbers = matchdb.extract_so_numbers_from_run_row(run)
    fo_key = fo_entity_key(
        run.get("distributor_name"),
        run.get("category"),
        run.get("season"),
    )
    payload = {
        "run": run,
        "so_numbers": so_numbers,
        "fo_entity_key": fo_key,
    }
    entity_key = fo_key if restore_scope == "run" and fo_key.strip("|") else f"run:{run_id}"
    _insert_archive(
        conn,
        user_id=user_id,
        kind="match_run",
        entity_key=entity_key,
        restore_scope=restore_scope,
        payload=payload,
        filled_order_id=int(run["filled_order_id"]) if run.get("filled_order_id") else None,
    )
    # Also snapshot each SO so partial re-upload can restore line-by-line.
    for so in so_numbers:
        archive_match_so(conn, user_id, run, so, restore_scope=restore_scope)


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


def archive_filled_order_item(
    conn: sqlite3.Connection,
    user_id: int,
    order: dict[str, Any],
    item: dict[str, Any],
    *,
    restore_scope: str = "entity",
) -> None:
    item_key = str(item.get("item_key") or item.get("id") or "").strip()
    if not item_key:
        return
    fo_key = fo_entity_key(
        order.get("distributor_name_raw"),
        order.get("category"),
        order.get("season"),
    )
    payload = {
        "order": order,
        "item": item,
        "fo_entity_key": fo_key,
        "filled_order_id": order.get("id"),
    }
    _insert_archive(
        conn,
        user_id=user_id,
        kind="filled_order_item",
        entity_key=f"{fo_key}|{item_key}",
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
    order_ref_no: str | None = None,
    original_path: str | None = None,
) -> None:
    if not recycle_rel_path:
        return
    payload = {
        "recycle_path": recycle_rel_path,
        "tracking_id": tracking_id,
        "kind_hint": kind_hint,
        "order_ref_no": (order_ref_no or "").strip() or None,
        "original_path": original_path,
    }
    entity = (order_ref_no or "").strip() or recycle_rel_path
    if kind_hint:
        entity = f"{entity}|{kind_hint}"
    _insert_archive(
        conn,
        user_id=user_id,
        kind="file",
        entity_key=entity,
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


_TRACKING_MERGE_COLS = [
    "distributor_id",
    "order_received_date",
    "order_filled_date",
    "sales_order_generated_date",
    "sales_order_file_reference",
    "sales_order_parsed",
    "sales_order_drive_file_id",
    "payment_status",
    "commercial_invoice_date",
    "commercial_invoice_file_reference",
    "commercial_invoice_parsed",
    "commercial_invoice_drive_file_id",
    "dispatch_date",
    "expected_delivery_date",
    "actual_delivery_date",
    "pod_number",
    "transit_status",
    "receiving_status",
    "receiving_condition",
    "order_sheet_id",
    "order_sheet_name",
]


def _row_has_value(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, str):
        return bool(val.strip())
    return True


def _insert_row_generic(
    conn: sqlite3.Connection,
    table: str,
    row: dict[str, Any],
    *,
    drop: set[str],
    overrides: dict[str, Any],
) -> None:
    data = {k: v for k, v in row.items() if k not in drop and k in row}
    data.update(overrides)
    if not data:
        return
    cols = list(data.keys())
    placeholders = ", ".join("?" for _ in cols)
    col_sql = ", ".join(cols)
    try:
        conn.execute(
            f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})",
            [data[c] for c in cols],
        )
    except sqlite3.IntegrityError:
        pass
    except sqlite3.OperationalError:
        pass


def restore_file_from_recycle(
    conn: sqlite3.Connection,
    user_id: int,
    order_ref_no: str,
    kind_hint: str,
    dest_dir_relative: str,
) -> str | None:
    """Move a recycled PDF back under upload root; return absolute restored path."""
    order_ref = (order_ref_no or "").strip()
    if not order_ref:
        return None
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, payload_json FROM order_desk_archive
        WHERE user_id = ? AND kind = 'file' AND restored_at IS NULL
          AND datetime(expires_at) > datetime('now')
          AND (entity_key = ? OR entity_key = ? OR payload_json LIKE ?)
        ORDER BY id DESC
        """,
        (
            user_id,
            f"{order_ref}|{kind_hint}",
            order_ref,
            f'%"order_ref_no": "{order_ref}"%',
        ),
    ).fetchall()
    root = upload_root()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if payload.get("kind_hint") and str(payload["kind_hint"]).lower() != kind_hint.lower():
            continue
        rel = payload.get("recycle_path")
        if not rel:
            continue
        src = root / rel
        if not src.is_file():
            continue
        dest_dir = (root / dest_dir_relative).resolve()
        try:
            dest_dir.relative_to(root)
        except ValueError:
            dest_dir = root / dest_dir_relative
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
            continue
        _mark_restored(conn, int(row["id"]))
        conn.commit()
        return str(dest)
    return None


def _merge_tracking_row(
    conn: sqlite3.Connection,
    tracking_id: int,
    archived: dict[str, Any],
    *,
    upload_kind: str,
    workspace_id: str,
) -> None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM order_lifecycle_tracking WHERE tracking_id = ?",
        (tracking_id,),
    ).fetchone()
    if not row:
        return
    current = dict(row)
    updates: dict[str, Any] = {}
    kind = upload_kind.lower()
    for col in _TRACKING_MERGE_COLS:
        arch_val = archived.get(col)
        cur_val = current.get(col)
        if not _row_has_value(arch_val):
            continue
        if col.startswith("sales_order") and kind == "so" and _row_has_value(cur_val):
            continue
        if col.startswith("commercial_invoice") and kind == "ci" and _row_has_value(cur_val):
            continue
        if _row_has_value(cur_val):
            continue
        if col in ("sales_order_parsed", "commercial_invoice_parsed") and isinstance(arch_val, dict):
            updates[col] = json.dumps(arch_val, default=str)
        else:
            updates[col] = arch_val
    if not updates:
        return
    set_sql = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE order_lifecycle_tracking SET {set_sql} WHERE tracking_id = ?",
        list(updates.values()) + [tracking_id],
    )


def restore_tracking_after_upload(
    conn: sqlite3.Connection,
    user_id: int,
    order_ref_no: str,
    tracking_id: int,
    workspace_id: str,
    *,
    upload_kind: str = "so",
) -> bool:
    """Full tracking restore: row merge, items, payments, achievements, processed docs, files."""
    order_ref = str(order_ref_no or "").strip()
    if not order_ref:
        return False
    archives = _pending_archives(conn, user_id, "tracking", entity_key=order_ref)
    if not archives:
        _restore_orphan_file_archives(conn, user_id, order_ref, tracking_id, upload_kind)
        return False
    archive_row = archives[-1]
    payload = archive_row["payload"]
    archived_tracking = payload.get("tracking") if isinstance(payload.get("tracking"), dict) else {}

    if archived_tracking:
        _merge_tracking_row(
            conn,
            tracking_id,
            archived_tracking,
            upload_kind=upload_kind,
            workspace_id=workspace_id,
        )

    existing_items = {
        (
            str(r[0] or "").strip().lower(),
            str(r[1] or "").strip().lower(),
        )
        for r in conn.execute(
            "SELECT item_name, item_key FROM order_fulfillment_items WHERE order_lifecycle_id = ?",
            (tracking_id,),
        ).fetchall()
    }
    for item in payload.get("fulfillment_items") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("item_name") or item.get("product_code") or "").strip().lower()
        key = str(item.get("item_key") or "").strip().lower()
        if (name, key) in existing_items or (name and (name, "") in existing_items):
            continue
        _insert_row_generic(
            conn,
            "order_fulfillment_items",
            item,
            drop={"id"},
            overrides={"order_lifecycle_id": tracking_id, "workspace_id": workspace_id or "default"},
        )

    for entry in payload.get("payment_entries") or []:
        if isinstance(entry, dict):
            _insert_row_generic(
                conn,
                "distributor_payment_entries",
                entry,
                drop={"id"},
                overrides={"tracking_id": tracking_id},
            )

    for ach in payload.get("achievements") or []:
        if isinstance(ach, dict):
            _insert_row_generic(
                conn,
                "achievements",
                ach,
                drop={"id"},
                overrides={
                    "order_lifecycle_tracking_id": tracking_id,
                    "workspace_id": workspace_id or "default",
                },
            )

    for doc in payload.get("processed_documents") or []:
        if not isinstance(doc, dict):
            continue
        doc_type = doc.get("document_type")
        doc_no = doc.get("document_number")
        ws = doc.get("workspace_id") or workspace_id or "default"
        if not doc_type or not doc_no:
            continue
        exists = conn.execute(
            "SELECT 1 FROM processed_documents "
            "WHERE workspace_id = ? AND document_type = ? AND document_number = ?",
            (ws, doc_type, doc_no),
        ).fetchone()
        if exists:
            continue
        _insert_row_generic(
            conn,
            "processed_documents",
            doc,
            drop={"id"},
            overrides={"tracking_id": tracking_id, "workspace_id": ws},
        )

    _restore_orphan_file_archives(conn, user_id, order_ref, tracking_id, upload_kind)

    _mark_restored(conn, archive_row["id"])
    conn.commit()
    return True


def _restore_orphan_file_archives(
    conn: sqlite3.Connection,
    user_id: int,
    order_ref_no: str,
    tracking_id: int,
    upload_kind: str,
) -> None:
    """Re-link recycled PDF paths onto the tracking row when file archives exist."""
    kind = "so" if upload_kind.lower() == "so" else "ci"
    restored = restore_file_from_recycle(conn, user_id, order_ref_no, kind, dest_dir_relative="restored")
    if not restored:
        return
    col = (
        "sales_order_file_reference"
        if kind == "so"
        else "commercial_invoice_file_reference"
    )
    conn.execute(
        f"UPDATE order_lifecycle_tracking SET {col} = ? WHERE tracking_id = ?",
        (restored, tracking_id),
    )


def restore_tracking_after_so_upload(
    conn: sqlite3.Connection,
    user_id: int,
    order_ref_no: str,
    tracking_id: int,
    workspace_id: str,
) -> bool:
    return restore_tracking_after_upload(
        conn, user_id, order_ref_no, tracking_id, workspace_id, upload_kind="so"
    )


def restore_match_run_archives_after_save(
    conn: sqlite3.Connection,
    user_id: int,
    run_id: int,
    filled_order_id: int | None,
    uploaded_so_numbers: list[str],
) -> int:
    """Merge archived whole-run snapshots for SO numbers present in this upload."""
    if not filled_order_id or not uploaded_so_numbers:
        return 0
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, payload_json FROM order_desk_archive
        WHERE user_id = ? AND kind = 'match_run' AND restored_at IS NULL
          AND filled_order_id = ? AND datetime(expires_at) > datetime('now')
        ORDER BY id ASC
        """,
        (user_id, filled_order_id),
    ).fetchall()
    if not rows:
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
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        snap = payload.get("run")
        if not isinstance(snap, dict):
            continue
        for so in payload.get("so_numbers") or []:
            key = (matchdb.normalize_so_number(so) or "").upper()
            if key not in uploaded_upper:
                continue
            piece = {
                "so_number": so,
                "so_line_detail": [
                    l
                    for l in (snap.get("so_line_detail") or [])
                    if isinstance(l, dict)
                    and (matchdb.normalize_so_number(l.get("so_number")) or "").upper() == key
                ],
                "rows": [
                    r
                    for r in (snap.get("rows") or [])
                    if isinstance(r, dict)
                    and any(
                        isinstance(c, dict)
                        and (matchdb.normalize_so_number(c.get("so_number")) or "").upper() == key
                        for c in (r.get("so_breakdown") or [])
                    )
                ],
            }
            merged = _merge_archived_so_into_run(run, piece)
            if merged:
                run = merged
                restored += 1
        _mark_restored(conn, int(row["id"]))
    if restored:
        _persist_run(conn, run_id, user_id, run)
        conn.commit()
    return restored


def restore_filled_order_after_upload(
    conn: sqlite3.Connection,
    user_id: int,
    filled_order_id: int,
    entity_key: str,
) -> int:
    """Restore archived FO header fields + line items onto a freshly uploaded FO."""
    import filled_orders_db as fodb

    fodb.ensure_schema(conn)
    restored = 0

    fo_archives = _pending_archives(conn, user_id, "filled_order", entity_key=entity_key)
    if fo_archives:
        payload = fo_archives[-1]["payload"]
        archived_items = payload.get("items") or []
        existing = conn.execute(
            "SELECT item_key FROM filled_order_items WHERE filled_order_id = ?",
            (filled_order_id,),
        ).fetchall()
        have = {str(r[0] or "").strip() for r in existing if r[0]}
        for item in archived_items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("item_key") or "").strip()
            if not key or key in have:
                continue
            fodb.insert_filled_order_item(conn, filled_order_id, item)
            have.add(key)
            restored += 1
        _mark_restored(conn, fo_archives[-1]["id"])

    item_archives = conn.execute(
        """
        SELECT id, payload_json FROM order_desk_archive
        WHERE user_id = ? AND kind = 'filled_order_item' AND restored_at IS NULL
          AND (filled_order_id = ? OR entity_key LIKE ?)
          AND datetime(expires_at) > datetime('now')
        ORDER BY id ASC
        """,
        (user_id, filled_order_id, f"{entity_key}|%"),
    ).fetchall()
    have = {
        str(r[0] or "").strip()
        for r in conn.execute(
            "SELECT item_key FROM filled_order_items WHERE filled_order_id = ?",
            (filled_order_id,),
        ).fetchall()
        if r[0]
    }
    prefix = f"{entity_key}|"
    for row in item_archives:
        try:
            payload = json.loads(row[1] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        item = payload.get("item")
        if not isinstance(item, dict):
            _mark_restored(conn, int(row[0]))
            continue
        key = str(item.get("item_key") or "").strip()
        if key and key not in have:
            fodb.insert_filled_order_item(conn, filled_order_id, item)
            have.add(key)
            restored += 1
        _mark_restored(conn, int(row[0]))

    if restored:
        fodb.recompute_order_counts(conn, filled_order_id)
        conn.commit()
    elif fo_archives or item_archives:
        conn.commit()
    return restored


def restore_match_after_fo_upload(
    conn: sqlite3.Connection,
    user_id: int,
    filled_order_id: int,
    entity_key: str,
) -> int:
    """Re-link or re-create FO↔SO Order Match when the same FO is re-uploaded."""
    import filled_orders_db as fodb
    from app.services.fo_so_match_lab import run_match_saved_fo_vs_so_pack

    fodb.ensure_schema(conn)
    fo = fodb.get_filled_order(conn, user_id, filled_order_id)
    if not fo:
        return 0

    relinked = matchdb.rematch_runs_for_fo_upload(
        conn, user_id, filled_order_id, entity_key
    )
    if relinked:
        return relinked

    existing = conn.execute(
        "SELECT id FROM fo_so_match_runs WHERE user_id = ? AND filled_order_id = ? LIMIT 1",
        (user_id, filled_order_id),
    ).fetchone()
    if existing:
        # Run is linked but rematch found nothing to update — do not duplicate.
        return 0

    key_lower = entity_key.strip().lower()
    archives = _pending_archives(conn, user_id, "match_run", entity_key=entity_key)
    if not archives:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, payload_json FROM order_desk_archive
            WHERE user_id = ? AND kind = 'match_run' AND restored_at IS NULL
              AND restore_scope = 'run' AND datetime(expires_at) > datetime('now')
            ORDER BY id DESC
            """,
            (user_id,),
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            snap = payload.get("run") if isinstance(payload.get("run"), dict) else {}
            snap_key = (
                payload.get("fo_entity_key")
                or fo_entity_key(
                    snap.get("distributor_name"),
                    snap.get("category"),
                    snap.get("season"),
                )
            )
            if str(snap_key).strip().lower() != key_lower:
                continue
            archives.append({"id": int(row["id"]), "payload": payload})

    if not archives:
        return 0

    restored = 0
    for archive_row in sorted(archives, key=lambda a: a["id"], reverse=True):
        payload = archive_row["payload"]
        snap = payload.get("run")
        if not isinstance(snap, dict):
            continue
        line_detail = [
            dict(l)
            for l in (snap.get("so_line_detail") or [])
            if isinstance(l, dict)
        ]
        if not line_detail:
            continue
        so_numbers = list(payload.get("so_numbers") or [])
        if not so_numbers:
            so_numbers = matchdb.extract_so_numbers_from_run_row(snap)
        conflicts = matchdb.find_so_number_conflicts(conn, so_numbers)
        if conflicts:
            continue

        fo_items = fodb.get_filled_order_items(conn, filled_order_id)
        so_pack: dict[str, Any] = {
            "line_detail": line_detail,
            "meta": {
                "source_filename": snap.get("so_source_filename"),
                "primary_buyer_name": snap.get("so_buyer_label"),
            },
        }
        match_payload = run_match_saved_fo_vs_so_pack(
            fo_meta={**fo, "id": filled_order_id},
            fo_items=fo_items,
            so_pack_payload=so_pack,
        )
        try:
            run = matchdb.save_match_run(
                conn,
                user_id=user_id,
                match_payload=match_payload,
                so_buyer_label=snap.get("so_buyer_label"),
                so_source_filename=snap.get("so_source_filename"),
                so_line_detail=line_detail,
                so_pack=so_pack,
            )
        except matchdb.DuplicateSalesOrderError:
            continue

        run_id = int(run["id"])
        restore_match_archives_after_save(
            conn, user_id, run_id, filled_order_id, so_numbers
        )
        restore_match_run_archives_after_save(
            conn, user_id, run_id, filled_order_id, so_numbers
        )
        _mark_restored(conn, archive_row["id"])
        for so in so_numbers:
            so_key = matchdb.normalize_so_number(so)
            if not so_key:
                continue
            for so_archive in _pending_archives(
                conn, user_id, "match_so", entity_key=so_key, filled_order_id=filled_order_id
            ):
                _mark_restored(conn, so_archive["id"])
        restored += 1
        break

    return restored


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
        WHERE user_id = ? AND kind IN ('match_so', 'match_run', 'filled_order', 'filled_order_item')
          AND (entity_key = ? OR entity_key LIKE ?) AND restored_at IS NULL
        """,
        (new_filled_order_id, user_id, entity_key, f"{entity_key}|%"),
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

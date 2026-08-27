"""Order Desk recycle store — no user delete is permanently destructive.

Every destructive Order Desk action (delete one SO from a match run, delete a
whole FO↔SO match, delete an SO/CI tracking row with its reconciliation items /
achievements / payment entries, delete a Filled Order, delete an uploaded file)
first snapshots what it is about to remove into `order_desk_archive`. Uploading
the same source data again then restores the snapshot, so the match, the
quantities and the totals come back to the pre-delete state instead of only
whatever the freshly uploaded file happens to contain.

Design rules
------------
* **One shared store, many kinds.** `kind` says what the payload is:

  | kind           | payload_json                                    | entity_key            | restored from            |
  |----------------|--------------------------------------------------|-----------------------|--------------------------|
  | `match_so`     | that SO's `so_line_detail` rows                  | SO number (upper)     | SO Pack upload for the FO |
  | `match_run`    | the run's metadata/totals (audit + rebuild hints)| `run:<id>`            | (metadata only)          |
  | `tracking`     | tracking row + order_fulfillment_items +
                     achievements + distributor_payment_entries +
                     processed_documents                              | order_ref_no (upper)  | SO PDF upload             |
  | `filled_order` | FO header + its filled_order_items               | `<dist>|<cat>|<season>` | FO workbook upload       |
  | `file`         | recycled-file reference                          | relative upload path  | tracking restore          |

* **Per-user isolation.** Every row carries the deleting user's `user_id` and
  every read filters on it, so user A's archive can never be restored into user
  B's data even for the same SO number / FO / order ref.
* **Newer data always wins.** Restore never touches an entity the caller is
  currently uploading (`exclude_*` arguments) and never overwrites a row that
  already exists, so a deliberate replace stays replaced.
* **Idempotent.** A restored row is stamped `restored_at` and skipped
  afterwards; restore also skips anything already present. Uploading the same
  file twice cannot double a quantity or a value.
* **`restore_scope`** decides how eager a restore is:
  * `run` — the destruction was wholesale (whole match run, bulk delete,
    tracking delete, FO delete), so any later upload for that FO / order ref may
    bring the rest of it back.
  * `entity` — the user singled this entity out (delete one SO, strip SO,
    replace with a revision). It only ever comes back if that exact SO number /
    order ref is uploaded again, so nothing the user meant to remove reappears
    behind his back.

Retention
---------
`RETENTION_DAYS` (90) — `purge_expired()` drops archive rows past retention and
deletes the recycled files they own. `maybe_purge()` is the cheap throttled
entry point wired into the existing Order Desk read path
(`order_match_list`), so cleanup happens without a cron or a new screen: at
most one purge per process per `_PURGE_INTERVAL_SECONDS` (6 h).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

RETENTION_DAYS = 90
_PURGE_INTERVAL_SECONDS = 6 * 3600
_last_purge_at = 0.0
_purge_lock = threading.Lock()

KIND_MATCH_SO = "match_so"
KIND_MATCH_RUN = "match_run"
KIND_TRACKING = "tracking"
KIND_FILLED_ORDER = "filled_order"
KIND_FILE = "file"

SCOPE_RUN = "run"
SCOPE_ENTITY = "entity"

RECYCLE_DIRNAME = "_nexora_recycle"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS order_desk_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    restore_scope TEXT NOT NULL DEFAULT 'entity',
    filled_order_id INTEGER,
    fo_key TEXT,
    run_id INTEGER,
    tracking_id INTEGER,
    so_number TEXT,
    source_filename TEXT,
    payload_json TEXT NOT NULL,
    meta_json TEXT,
    reason TEXT,
    content_hash TEXT,
    deleted_at TEXT NOT NULL,
    restored_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_order_desk_archive_lookup
    ON order_desk_archive(user_id, kind, entity_key);
CREATE INDEX IF NOT EXISTS idx_order_desk_archive_fo
    ON order_desk_archive(user_id, kind, filled_order_id);
CREATE INDEX IF NOT EXISTS idx_order_desk_archive_fokey
    ON order_desk_archive(user_id, kind, fo_key);
CREATE INDEX IF NOT EXISTS idx_order_desk_archive_deleted
    ON order_desk_archive(deleted_at);
"""

_ARCHIVE_COLUMNS = [
    "workspace_id", "user_id", "kind", "entity_key", "restore_scope",
    "filled_order_id", "fo_key", "run_id", "tracking_id", "so_number",
    "source_filename", "payload_json", "meta_json", "reason", "content_hash",
    "deleted_at", "restored_at",
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent schema + additive column migrations (repo convention)."""
    conn.executescript(SCHEMA_SQL)
    for column, ddl in (
        ("restore_scope", "TEXT NOT NULL DEFAULT 'entity'"),
        ("fo_key", "TEXT"),
        ("content_hash", "TEXT"),
        ("restored_at", "TEXT"),
        ("source_filename", "TEXT"),
        ("meta_json", "TEXT"),
    ):
        _ensure_column_exists(conn, "order_desk_archive", column, ddl)
    conn.commit()


def _ensure_column_exists(
    conn: sqlite3.Connection, table: str, column: str, ddl: str
) -> None:
    try:
        existing = {
            str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
    except sqlite3.OperationalError:
        return
    if column in existing:
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    except sqlite3.OperationalError:
        pass


def _norm_key(raw: Any) -> str:
    return str(raw or "").strip().upper()


def fo_key_for(
    distributor_id: Any, category: Any, season: Any
) -> str:
    """Stable identity of a Filled Order independent of its row id.

    A deleted FO that is uploaded again gets a *new* `filled_order_id`, so
    archived rows are also matched on this key to survive that.
    """
    return "|".join(
        [
            str(distributor_id if distributor_id is not None else ""),
            str(category or "").strip().lower(),
            str(season or "").strip().lower(),
        ]
    )


def _hash_payload(payload: Any) -> str:
    blob = json.dumps(payload, default=str, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _insert(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    kind: str,
    entity_key: str,
    payload: Any,
    workspace_id: str | None = None,
    restore_scope: str = SCOPE_ENTITY,
    filled_order_id: Any = None,
    fo_key: str | None = None,
    run_id: Any = None,
    tracking_id: Any = None,
    so_number: str | None = None,
    source_filename: str | None = None,
    meta: Any = None,
    reason: str | None = None,
) -> int:
    ensure_schema(conn)
    conn.execute(
        f"""
        INSERT INTO order_desk_archive ({", ".join(_ARCHIVE_COLUMNS)})
        VALUES ({", ".join("?" for _ in _ARCHIVE_COLUMNS)})
        """,
        (
            workspace_id,
            int(user_id),
            kind,
            entity_key,
            restore_scope,
            int(filled_order_id) if filled_order_id is not None else None,
            fo_key,
            int(run_id) if run_id is not None else None,
            int(tracking_id) if tracking_id is not None else None,
            so_number,
            source_filename,
            json.dumps(payload, default=str),
            json.dumps(meta, default=str) if meta is not None else None,
            reason,
            _hash_payload(payload),
            _now(),
            None,
        ),
    )
    conn.commit()
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _mark_restored(conn: sqlite3.Connection, ids: Iterable[int]) -> None:
    ids = [int(i) for i in ids]
    if not ids:
        return
    now = _now()
    conn.executemany(
        "UPDATE order_desk_archive SET restored_at = ? WHERE id = ?",
        [(now, i) for i in ids],
    )
    conn.commit()


# ------------------------------------------------------------ FO ↔ SO match


def _lines_of(run: dict[str, Any] | None) -> list[dict[str, Any]]:
    detail = (run or {}).get("so_line_detail") or []
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            detail = []
    return [r for r in detail if isinstance(r, dict)]


def _run_meta(run: dict[str, Any]) -> dict[str, Any]:
    return {
        k: run.get(k)
        for k in (
            "id", "filled_order_id", "distributor_id", "distributor_name",
            "category", "season", "fo_source_filename", "so_buyer_label",
            "so_source_filename", "fo_qty", "so_qty", "delta_qty",
            "fo_exmill_value", "so_net_amount", "delta_value", "match_count",
            "fuzzy_count", "mismatch_count", "missing_count", "extra_count",
            "created_at",
        )
    }


def archive_match_so(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    run: dict[str, Any],
    so_numbers: Iterable[str],
    reason: str,
    restore_scope: str = SCOPE_ENTITY,
    workspace_id: str | None = None,
) -> list[int]:
    """Snapshot the given SO numbers' lines out of one match run."""
    from app.services import fo_so_match_db as matchdb

    lines = _lines_of(run)
    meta = _run_meta(run)
    key = fo_key_for(run.get("distributor_id"), run.get("category"), run.get("season"))
    wanted = {_norm_key(matchdb.normalize_so_number(n)) for n in so_numbers}
    wanted.discard("")
    archived: list[int] = []
    for so_n in sorted(wanted):
        so_lines = [
            r
            for r in lines
            if _norm_key(matchdb.normalize_so_number(r.get("so_number"))) == so_n
        ]
        if not so_lines:
            continue
        archived.append(
            _insert(
                conn,
                user_id=user_id,
                workspace_id=workspace_id,
                kind=KIND_MATCH_SO,
                entity_key=so_n,
                restore_scope=restore_scope,
                filled_order_id=run.get("filled_order_id"),
                fo_key=key,
                run_id=run.get("id"),
                so_number=so_n,
                source_filename=run.get("so_source_filename"),
                payload=so_lines,
                meta=meta,
                reason=reason,
            )
        )
    return archived


def archive_match_run(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    run: dict[str, Any],
    reason: str,
    workspace_id: str | None = None,
) -> list[int]:
    """Snapshot a whole match run: every SO in it, plus the run metadata.

    A whole-run delete is wholesale, so the SO snapshots get `restore_scope=run`
    — any later SO Pack upload against that Filled Order brings the rest back.
    """
    from app.services import fo_so_match_db as matchdb

    numbers = {
        _norm_key(matchdb.normalize_so_number(r.get("so_number")))
        for r in _lines_of(run)
    }
    numbers.discard("")
    ids = archive_match_so(
        conn,
        user_id=user_id,
        run=run,
        so_numbers=numbers,
        reason=reason,
        restore_scope=SCOPE_RUN,
        workspace_id=workspace_id,
    )
    meta = _run_meta(run)
    ids.append(
        _insert(
            conn,
            user_id=user_id,
            workspace_id=workspace_id,
            kind=KIND_MATCH_RUN,
            entity_key=f"run:{run.get('id')}",
            restore_scope=SCOPE_RUN,
            filled_order_id=run.get("filled_order_id"),
            fo_key=fo_key_for(
                run.get("distributor_id"), run.get("category"), run.get("season")
            ),
            run_id=run.get("id"),
            source_filename=run.get("so_source_filename"),
            payload={"rows": run.get("rows") or [], "so_numbers": sorted(numbers)},
            meta=meta,
            reason=reason,
        )
    )
    return ids


def _archived_match_candidates(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    filled_order_id: Any,
    fo_key: str | None,
    incoming: set[str],
) -> list[tuple[int, str, list[dict[str, Any]]]]:
    ensure_schema(conn)
    sql = (
        "SELECT id, entity_key, restore_scope, payload_json FROM order_desk_archive "
        "WHERE user_id = ? AND kind = ? AND restored_at IS NULL AND ("
        "  (filled_order_id IS NOT NULL AND filled_order_id = ?)"
        "  OR (fo_key IS NOT NULL AND fo_key = ?)"
        ") ORDER BY id DESC"
    )
    rows = conn.execute(
        sql,
        (
            int(user_id),
            KIND_MATCH_SO,
            int(filled_order_id) if filled_order_id is not None else -1,
            fo_key or "\u0000",
        ),
    ).fetchall()
    out: list[tuple[int, str, list[dict[str, Any]]]] = []
    seen: set[str] = set()
    for row_id, entity_key, scope, payload_json in rows:
        key = _norm_key(entity_key)
        if not key or key in seen:
            continue
        eligible = scope == SCOPE_RUN or key in incoming
        if not eligible:
            continue
        try:
            lines = json.loads(payload_json)
        except ValueError:
            continue
        if not isinstance(lines, list) or not lines:
            continue
        seen.add(key)
        out.append((int(row_id), key, [r for r in lines if isinstance(r, dict)]))
    return out


def restore_match_for_fo(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    filled_order_id: int,
    fo_key: str | None,
    incoming_so_numbers: Iterable[str] = (),
) -> dict[str, Any] | None:
    """Bring archived SO lines of this Filled Order back into its match run.

    Called from the SO Pack upload path *before* the incoming pack is matched,
    so the rest of that route sees the restored state and merges the upload on
    top of it.

    Never restores an SO number that is in the current upload (the file is the
    newer truth) or that the run already carries, which is what makes a repeated
    upload idempotent.
    """
    from app.services import fo_so_match_db as matchdb
    from app.services import fo_so_revision as sorev

    incoming = {_norm_key(matchdb.normalize_so_number(n)) for n in incoming_so_numbers}
    incoming.discard("")

    candidates = _archived_match_candidates(
        conn,
        user_id=user_id,
        filled_order_id=filled_order_id,
        fo_key=fo_key,
        incoming=incoming,
    )
    if not candidates:
        return None

    existing = sorev.get_latest_run_for_fo(
        conn, user_id=user_id, filled_order_id=int(filled_order_id)
    )
    existing_lines = _lines_of(existing)
    present = {
        _norm_key(matchdb.normalize_so_number(r.get("so_number")))
        for r in existing_lines
    }
    present.discard("")

    restore_ids: list[int] = []
    restored_numbers: list[str] = []
    add_lines: list[dict[str, Any]] = []
    for row_id, so_key, lines in candidates:
        if so_key in incoming:
            # The upload itself carries this SO — retire the snapshot silently.
            restore_ids.append(row_id)
            continue
        if so_key in present:
            # Already back (idempotency) — nothing to add.
            restore_ids.append(row_id)
            continue
        add_lines.extend(lines)
        restored_numbers.append(so_key)
        restore_ids.append(row_id)

    if not add_lines:
        _mark_restored(conn, restore_ids)
        return None

    # An SO number claimed elsewhere must not be stolen back by a restore.
    conflicts = {
        _norm_key(c.get("so_number"))
        for c in matchdb.find_so_number_conflicts(
            conn,
            restored_numbers,
            exclude_run_id=int(existing["id"]) if existing else None,
        )
    }
    if conflicts:
        add_lines = [
            r
            for r in add_lines
            if _norm_key(matchdb.normalize_so_number(r.get("so_number")))
            not in conflicts
        ]
        restored_numbers = [n for n in restored_numbers if n not in conflicts]
        if not add_lines:
            return None

    merged = existing_lines + add_lines
    if existing:
        run = sorev.rebuild_run_from_lines(
            conn,
            user_id=int(user_id),
            run_id=int(existing["id"]),
            lines=merged,
        )
    else:
        run = _save_new_run_from_lines(
            conn,
            user_id=int(user_id),
            filled_order_id=int(filled_order_id),
            lines=merged,
        )
    if run is None:
        return None
    _mark_restored(conn, restore_ids)
    return {"run": run, "restored_so_numbers": sorted(restored_numbers)}


def _save_new_run_from_lines(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    filled_order_id: int,
    lines: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Recreate a match run for an FO whose run row itself was deleted."""
    import filled_orders_db as fodb

    from app.services import fo_so_match_db as matchdb
    from app.services import fo_so_revision as sorev
    from app.services.fo_so_match_lab import run_match_saved_fo_vs_so_pack

    fodb.ensure_schema(conn)
    fo = fodb.get_filled_order(conn, int(user_id), int(filled_order_id))
    if not fo:
        return None
    items = fodb.get_filled_order_items(conn, int(filled_order_id))
    pack = sorev.pack_from_lines(lines, source_filename="restored_from_archive")
    result = run_match_saved_fo_vs_so_pack(
        fo_meta=fo, fo_items=items, so_pack_payload=pack
    )
    try:
        return matchdb.save_match_run(
            conn,
            user_id=int(user_id),
            match_payload=result,
            so_buyer_label=fo.get("distributor_name_raw"),
            so_source_filename=None,
            so_line_detail=lines,
            so_pack=pack,
        )
    except matchdb.DuplicateSalesOrderError:
        return None


# ---------------------------------------------------------------- tracking

_TRACKING_CHILDREN = (
    ("order_fulfillment_items", "order_lifecycle_id"),
    ("achievements", "order_lifecycle_tracking_id"),
    ("distributor_payment_entries", "tracking_id"),
    ("processed_documents", "tracking_id"),
)


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    except sqlite3.OperationalError:
        return []


def _dump_rows(conn: sqlite3.Connection, table: str, column: str, value: Any) -> list[dict]:
    cols = _table_columns(conn, table)
    if not cols:
        return []
    try:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} WHERE {column} = ?", (value,)
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(cols, r)) for r in rows]


def archive_tracking(
    db_path: str,
    *,
    user_id: int | None,
    workspace_id: str | None,
    tracking: dict[str, Any],
    reason: str,
    restore_scope: str = SCOPE_RUN,
) -> list[int]:
    """Snapshot an SO/CI tracking row and everything hanging off it.

    Captured: the `order_lifecycle_tracking` row, its
    `order_fulfillment_items` reconciliation lines, `achievements`,
    `distributor_payment_entries` and `processed_documents` guards.
    """
    if user_id is None:
        return []
    tracking_id = tracking.get("tracking_id")
    if tracking_id is None:
        return []
    conn = sqlite3.connect(db_path)
    try:
        payload: dict[str, Any] = {
            "tracking": _dump_rows(
                conn, "order_lifecycle_tracking", "tracking_id", tracking_id
            ),
        }
        for table, column in _TRACKING_CHILDREN:
            payload[table] = _dump_rows(conn, table, column, tracking_id)
        return [
            _insert(
                conn,
                user_id=int(user_id),
                workspace_id=workspace_id,
                kind=KIND_TRACKING,
                entity_key=_norm_key(tracking.get("order_ref_no")),
                restore_scope=restore_scope,
                tracking_id=int(tracking_id),
                source_filename=tracking.get("sales_order_file_reference"),
                payload=payload,
                meta={
                    "order_ref_no": tracking.get("order_ref_no"),
                    "distributor_id": tracking.get("distributor_id"),
                    "sales_order_file_reference": tracking.get(
                        "sales_order_file_reference"
                    ),
                    "commercial_invoice_file_reference": tracking.get(
                        "commercial_invoice_file_reference"
                    ),
                },
                reason=reason,
            )
        ]
    finally:
        conn.close()


def restore_tracking_for_upload(
    db_path: str,
    *,
    user_id: int | None,
    workspace_id: str | None,
    order_ref_no: str,
    tracking_id: int,
) -> dict[str, Any] | None:
    """Re-attach an archived tracking row's data to a freshly uploaded SO.

    The re-uploaded SO PDF is the newer truth for the tracking row itself; what
    would otherwise be lost forever are the derived rows (reconciliation items,
    achievements, payment entries) and the CI that was linked to it. Those are
    restored onto the new `tracking_id`.

    Idempotent: items are keyed by `item_key`/`item_name` and only inserted when
    absent, achievements / payment entries only when the new tracking row has
    none, and the archive row is stamped restored.
    """
    if user_id is None or not order_ref_no:
        return None
    key = _norm_key(order_ref_no)
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT id, payload_json FROM order_desk_archive "
            "WHERE user_id = ? AND kind = ? AND entity_key = ? AND restored_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (int(user_id), KIND_TRACKING, key),
        ).fetchone()
        if not row:
            return None
        archive_id = int(row[0])
        try:
            payload = json.loads(row[1])
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None

        restored = {"items": 0, "achievements": 0, "payment_entries": 0, "ci_relinked": False}

        items = [r for r in (payload.get("order_fulfillment_items") or []) if isinstance(r, dict)]
        if items:
            restored["items"] = _restore_child_items(conn, tracking_id, items)
        for table, column, counter in (
            ("achievements", "order_lifecycle_tracking_id", "achievements"),
            ("distributor_payment_entries", "tracking_id", "payment_entries"),
        ):
            rows = [r for r in (payload.get(table) or []) if isinstance(r, dict)]
            if not rows:
                continue
            existing = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (int(tracking_id),)
            ).fetchone()[0]
            if int(existing or 0) > 0:
                continue
            restored[counter] = _reinsert_rows(conn, table, rows, {column: int(tracking_id)})

        restored["ci_relinked"] = _restore_ci_link(conn, tracking_id, payload)
        conn.commit()
        _mark_restored(conn, [archive_id])
        return restored
    finally:
        conn.close()


def _restore_child_items(
    conn: sqlite3.Connection, tracking_id: int, items: list[dict[str, Any]]
) -> int:
    """Insert archived reconciliation items that the new tracking row lacks."""
    cols = _table_columns(conn, "order_fulfillment_items")
    if not cols:
        return 0
    present: set[str] = set()
    for r in conn.execute(
        "SELECT COALESCE(item_key, ''), COALESCE(item_name, '') "
        "FROM order_fulfillment_items WHERE order_lifecycle_id = ?",
        (int(tracking_id),),
    ).fetchall():
        present.add(_norm_key(r[0]) or _norm_key(r[1]))
    fresh = [
        r
        for r in items
        if (_norm_key(r.get("item_key")) or _norm_key(r.get("item_name")))
        not in present
    ]
    if not fresh:
        return 0
    return _reinsert_rows(
        conn, "order_fulfillment_items", fresh, {"order_lifecycle_id": int(tracking_id)}
    )


def _reinsert_rows(
    conn: sqlite3.Connection,
    table: str,
    rows: list[dict[str, Any]],
    overrides: dict[str, Any],
) -> int:
    """Re-insert archived rows verbatim minus their primary key."""
    cols = _table_columns(conn, table)
    if not cols:
        return 0
    pk = _primary_key(conn, table)
    usable = [c for c in cols if c != pk]
    inserted = 0
    for row in rows:
        values = []
        for c in usable:
            values.append(overrides[c] if c in overrides else row.get(c))
        try:
            conn.execute(
                f"INSERT INTO {table} ({', '.join(usable)}) "
                f"VALUES ({', '.join('?' for _ in usable)})",
                tuple(values),
            )
            inserted += 1
        except sqlite3.Error:
            continue
    return inserted


def _primary_key(conn: sqlite3.Connection, table: str) -> str | None:
    try:
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall():
            if int(r[5] or 0) == 1:
                return str(r[1])
    except sqlite3.OperationalError:
        return None
    return None


def _restore_ci_link(
    conn: sqlite3.Connection, tracking_id: int, payload: dict[str, Any]
) -> bool:
    """Put back the Commercial Invoice that was linked before the delete."""
    archived = [r for r in (payload.get("tracking") or []) if isinstance(r, dict)]
    if not archived:
        return False
    old = archived[0]
    ci_ref = old.get("commercial_invoice_file_reference")
    if not ci_ref:
        return False
    row = conn.execute(
        "SELECT COALESCE(commercial_invoice_file_reference, '') "
        "FROM order_lifecycle_tracking WHERE tracking_id = ?",
        (int(tracking_id),),
    ).fetchone()
    if row is None or str(row[0] or "").strip():
        return False
    restored_ref = restore_recycled_file(str(ci_ref)) or str(ci_ref)
    try:
        conn.execute(
            "UPDATE order_lifecycle_tracking SET commercial_invoice_file_reference = ?, "
            "commercial_invoice_parsed = COALESCE(commercial_invoice_parsed, ?), "
            "commercial_invoice_date = COALESCE(commercial_invoice_date, ?) "
            "WHERE tracking_id = ?",
            (
                restored_ref,
                old.get("commercial_invoice_parsed"),
                old.get("commercial_invoice_date"),
                int(tracking_id),
            ),
        )
    except sqlite3.Error:
        return False
    return True


# ------------------------------------------------------------ filled orders


def archive_filled_order(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    filled_order_id: int,
    reason: str,
    workspace_id: str | None = None,
) -> list[int]:
    """Snapshot an FO header + its lines before it is deleted or replaced."""
    import filled_orders_db as fodb

    fodb.ensure_schema(conn)
    fo = fodb.get_filled_order(conn, int(user_id), int(filled_order_id))
    if not fo:
        return []
    items = _dump_rows(conn, "filled_order_items", "filled_order_id", int(filled_order_id))
    key = fo_key_for(fo.get("distributor_id"), fo.get("category"), fo.get("season"))
    return [
        _insert(
            conn,
            user_id=int(user_id),
            workspace_id=workspace_id,
            kind=KIND_FILLED_ORDER,
            entity_key=key,
            restore_scope=SCOPE_RUN,
            filled_order_id=int(filled_order_id),
            fo_key=key,
            source_filename=fo.get("source_filename"),
            payload={"filled_order": fo, "filled_order_items": items},
            meta={
                "distributor_id": fo.get("distributor_id"),
                "category": fo.get("category"),
                "season": fo.get("season"),
            },
            reason=reason,
        )
    ]


def relink_archives_to_new_filled_order(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    filled_order_id: int,
    distributor_id: Any,
    category: Any,
    season: Any,
) -> int:
    """Point this user's archives for that FO identity at the new FO row id.

    A re-uploaded Filled Order gets a fresh `filled_order_id`; without this the
    archived SO snapshots would still reference the deleted id and never come
    back.
    """
    ensure_schema(conn)
    key = fo_key_for(distributor_id, category, season)
    cur = conn.execute(
        "UPDATE order_desk_archive SET filled_order_id = ? "
        "WHERE user_id = ? AND fo_key = ? AND restored_at IS NULL",
        (int(filled_order_id), int(user_id), key),
    )
    conn.commit()
    return int(cur.rowcount or 0)


# ------------------------------------------------------------------- files


def upload_root() -> Path:
    return (
        Path("app/instance/order_fulfillment_files")
        if Path("app/instance").exists()
        else Path("instance/order_fulfillment_files")
    ).resolve()


def recycle_root() -> Path:
    return upload_root() / RECYCLE_DIRNAME


def recycle_file(
    file_reference: Any,
    *,
    conn: sqlite3.Connection | None = None,
    user_id: int | None = None,
    workspace_id: str | None = None,
    reason: str = "",
) -> str | None:
    """Move an uploaded file into the recycle area instead of unlinking it.

    Returns the recycled absolute path, or None when there was nothing to move
    (missing file, or a path outside the upload root — those are left alone,
    exactly like the previous delete guard did).
    """
    if not file_reference:
        return None
    root = upload_root()
    try:
        path = Path(str(file_reference)).resolve()
        relative = path.relative_to(root)
    except (ValueError, OSError):
        return None
    if RECYCLE_DIRNAME in relative.parts:
        return str(path)
    if not path.exists() or not path.is_file():
        return None
    bucket = recycle_root() / str(user_id if user_id is not None else "shared")
    target = bucket / relative
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        shutil.move(str(path), str(target))
    except OSError:
        return None
    if conn is not None and user_id is not None:
        _insert(
            conn,
            user_id=int(user_id),
            workspace_id=workspace_id,
            kind=KIND_FILE,
            entity_key=str(relative).replace("\\", "/"),
            restore_scope=SCOPE_ENTITY,
            source_filename=path.name,
            payload={
                "original_path": str(path),
                "recycled_path": str(target),
                "relative_path": str(relative).replace("\\", "/"),
            },
            reason=reason,
        )
    return str(target)


def keep_upload_for_support(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    workspace_id: str | None,
    filename: str,
    file_bytes: bytes,
    reason: str = "",
    max_bytes: int = 25 * 1024 * 1024,
) -> str | None:
    """Park an uploaded pack in the recycle area so support can re-read it.

    Uploads that fail to parse used to be thrown away with the request, which
    left nothing to debug unless the user still had the file and was willing to
    send it. Storing it here reuses the existing retention: `purge_expired()`
    deletes it with its archive row after `RETENTION_DAYS`.
    """
    if not file_bytes or not filename:
        return None
    if len(file_bytes) > int(max_bytes):
        return None
    safe = Path(str(filename).replace("\\", "/")).name or "so_pack"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    relative = Path("so_pack_support") / f"{stamp}_{safe}"
    target = recycle_root() / str(int(user_id)) / relative
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file_bytes)
    except OSError:
        return None
    _insert(
        conn,
        user_id=int(user_id),
        workspace_id=workspace_id,
        kind=KIND_FILE,
        entity_key=str(relative).replace("\\", "/"),
        restore_scope=SCOPE_ENTITY,
        source_filename=safe,
        payload={
            "recycled_path": str(target),
            "relative_path": str(relative).replace("\\", "/"),
            "bytes": len(file_bytes),
        },
        reason=reason or "so_pack_upload_kept_for_support",
    )
    return str(target)


def restore_recycled_file(file_reference: Any) -> str | None:
    """Move a recycled file back to its original location, if it is still there."""
    if not file_reference:
        return None
    root = upload_root()
    try:
        original = Path(str(file_reference)).resolve()
        relative = original.relative_to(root)
    except (ValueError, OSError):
        return None
    if original.exists():
        return str(original)
    for bucket in sorted(recycle_root().glob("*")) if recycle_root().exists() else []:
        candidate = bucket / relative
        if candidate.exists() and candidate.is_file():
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(candidate), str(original))
                return str(original)
            except OSError:
                return None
    return None


def recycle_file_references(
    file_references: Any,
    *,
    db_path: str | None = None,
    user_id: int | None = None,
    workspace_id: str | None = None,
    reason: str = "",
) -> list[str]:
    """Recycle every file reference of a deleted record (dict or iterable)."""
    if not file_references:
        return []
    values = (
        list(file_references.values())
        if isinstance(file_references, dict)
        else list(file_references)
    )
    conn = sqlite3.connect(db_path) if (db_path and user_id is not None) else None
    try:
        moved: list[str] = []
        for ref in values:
            got = recycle_file(
                ref,
                conn=conn,
                user_id=user_id,
                workspace_id=workspace_id,
                reason=reason,
            )
            if got:
                moved.append(got)
        return moved
    finally:
        if conn is not None:
            conn.close()


# --------------------------------------------------------------- retention


def purge_expired(
    conn: sqlite3.Connection, *, days: int = RETENTION_DAYS
) -> dict[str, int]:
    """Drop archive rows past retention and delete the files they own."""
    ensure_schema(conn)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    stale = conn.execute(
        "SELECT id, kind, payload_json FROM order_desk_archive WHERE deleted_at < ?",
        (cutoff,),
    ).fetchall()
    files_removed = 0
    for _row_id, kind, payload_json in stale:
        if kind != KIND_FILE:
            continue
        try:
            payload = json.loads(payload_json)
        except ValueError:
            continue
        recycled = (payload or {}).get("recycled_path")
        if not recycled:
            continue
        try:
            p = Path(str(recycled))
            if p.exists() and RECYCLE_DIRNAME in p.resolve().parts:
                p.unlink()
                files_removed += 1
        except OSError:
            continue
    conn.execute("DELETE FROM order_desk_archive WHERE deleted_at < ?", (cutoff,))
    conn.commit()
    return {"rows_purged": len(stale), "files_removed": files_removed}


def maybe_purge(conn: sqlite3.Connection, *, days: int = RETENTION_DAYS) -> None:
    """Throttled retention cleanup for the normal Order Desk read path."""
    global _last_purge_at
    now = time.time()
    if now - _last_purge_at < _PURGE_INTERVAL_SECONDS:
        return
    if not _purge_lock.acquire(blocking=False):
        return
    try:
        if now - _last_purge_at < _PURGE_INTERVAL_SECONDS:
            return
        _last_purge_at = now
        purge_expired(conn, days=days)
    except Exception:
        pass
    finally:
        _purge_lock.release()

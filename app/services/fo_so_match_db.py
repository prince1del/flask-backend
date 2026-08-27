"""Persist FO ↔ SO Pack match runs (Order Desk — Order Match page)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fo_so_match_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    filled_order_id INTEGER,
    distributor_id INTEGER,
    distributor_name TEXT,
    category TEXT,
    season TEXT,
    fo_source_filename TEXT,
    so_buyer_label TEXT,
    so_source_filename TEXT,
    fo_qty REAL,
    so_qty REAL,
    delta_qty REAL,
    fo_exmill_value REAL,
    so_net_amount REAL,
    delta_value REAL,
    match_count INTEGER DEFAULT 0,
    fuzzy_count INTEGER DEFAULT 0,
    mismatch_count INTEGER DEFAULT 0,
    missing_count INTEGER DEFAULT 0,
    extra_count INTEGER DEFAULT 0,
    rows_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fo_so_match_user
    ON fo_so_match_runs(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_fo_so_match_dist
    ON fo_so_match_runs(user_id, distributor_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_fo_so_match_fo
    ON fo_so_match_runs(user_id, filled_order_id, id DESC);

-- One Sales Order number may appear in at most one saved match run.
CREATE TABLE IF NOT EXISTS fo_so_match_so_index (
    so_number TEXT NOT NULL PRIMARY KEY,
    run_id INTEGER NOT NULL,
    user_id INTEGER,
    filled_order_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fo_so_match_so_run
    ON fo_so_match_so_index(run_id);
"""

_schema_ensured = False


class DuplicateSalesOrderError(ValueError):
    """Raised when an SO number is already saved in another match run."""

    def __init__(self, conflicts: list[dict[str, Any]]):
        self.conflicts = conflicts
        samples = ", ".join(
            str(c.get("so_number") or "") for c in conflicts[:5] if c.get("so_number")
        )
        extra = f" (+{len(conflicts) - 5} more)" if len(conflicts) > 5 else ""
        super().__init__(
            f"Sales Order already uploaded: {samples}{extra}. "
            "Each SO is allowed only once — delete it from Order Desk → Sales Orders first, "
            "then upload again."
        )


class SoAlreadyInSystemError(ValueError):
    """Same SO number + same qty/value/lines — no duplicate write."""

    def __init__(self, compare: dict[str, Any]):
        self.compare = compare
        nums = ", ".join(
            str(n) for n in (compare.get("so_numbers") or [])[:5] if n
        ) or "SO"
        super().__init__(f"SO already in system ({nums}) — no change detected.")


class SoReplaceConfirmationRequired(ValueError):
    """Same SO number but qty/value/lines differ — UI must confirm replace."""

    def __init__(self, compare: dict[str, Any]):
        self.compare = compare
        super().__init__(
            "Sales Order revision detected — confirm replace old SO with new SO."
        )


class SoSplitOrAdditionalRequired(ValueError):
    """New SO number overlaps FO articles already covered by another SO on this FO."""

    def __init__(self, compare: dict[str, Any]):
        self.compare = compare
        super().__init__(
            "This SO overlaps an existing SO on this FO — choose Additional order or SO split."
        )


def ensure_schema(conn: sqlite3.Connection) -> None:
    global _schema_ensured
    if _schema_ensured:
        try:
            conn.execute("SELECT 1 FROM fo_so_match_runs LIMIT 1")
            conn.execute("SELECT so_line_detail_json FROM fo_so_match_runs LIMIT 1")
            conn.execute("SELECT 1 FROM fo_so_match_so_index LIMIT 1")
            return
        except sqlite3.OperationalError:
            _schema_ensured = False
    conn.executescript(SCHEMA_SQL)
    try:
        conn.execute(
            "ALTER TABLE fo_so_match_runs ADD COLUMN so_line_detail_json TEXT"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.commit()
    deleted = _cleanup_duplicate_runs_by_filled_order(conn)
    if deleted:
        try:
            conn.execute("DELETE FROM fo_so_match_so_index")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    _rebuild_so_index_if_empty(conn)
    _schema_ensured = True


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _row_to_dict(row: sqlite3.Row | tuple, keys: list[str]) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return {k: row[k] for k in keys}
    return dict(zip(keys, row))


RUN_COLUMNS = [
    "id", "user_id", "filled_order_id", "distributor_id", "distributor_name",
    "category", "season", "fo_source_filename", "so_buyer_label", "so_source_filename",
    "fo_qty", "so_qty", "delta_qty", "fo_exmill_value", "so_net_amount", "delta_value",
    "match_count", "fuzzy_count", "mismatch_count", "missing_count", "extra_count",
    "rows_json", "so_line_detail_json", "created_at",
]


def normalize_so_number(raw: Any) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    # Strip common "SO " / "SO#" prefixes for stable uniqueness.
    upper = text.upper()
    if upper.startswith("SO#"):
        text = text[3:].strip()
    elif upper.startswith("SO ") or upper.startswith("SO-"):
        text = text[3:].strip()
    return text or None


def extract_so_numbers_from_pack(so_pack: dict[str, Any] | None) -> list[str]:
    """Unique SO contract numbers from analyze payload."""
    if not isinstance(so_pack, dict):
        return []
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        n = normalize_so_number(raw)
        if not n:
            return
        key = n.upper()
        if key in seen:
            return
        seen.add(key)
        found.append(n)

    for row in so_pack.get("line_detail") or []:
        if isinstance(row, dict):
            add(row.get("so_number"))
    for row in so_pack.get("consolidated") or []:
        if isinstance(row, dict):
            add(row.get("so_number"))
    for row in so_pack.get("so_summary") or []:
        if isinstance(row, dict):
            add(row.get("so_number"))
    return found


def extract_so_numbers_from_run_row(run: dict[str, Any]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        n = normalize_so_number(raw)
        if not n:
            return
        key = n.upper()
        if key in seen:
            return
        seen.add(key)
        found.append(n)

    detail = run.get("so_line_detail")
    if isinstance(detail, str) and detail.strip():
        try:
            detail = json.loads(detail)
        except Exception:
            detail = []
    if isinstance(detail, list):
        for row in detail:
            if isinstance(row, dict):
                add(row.get("so_number"))

    rows = run.get("rows")
    if rows is None and isinstance(run.get("rows_json"), str):
        try:
            rows = json.loads(run["rows_json"])
        except Exception:
            rows = []
    if isinstance(rows, list):
        for r in rows:
            if not isinstance(r, dict):
                continue
            for n in r.get("so_numbers") or []:
                add(n)
            for cell in r.get("so_breakdown") or []:
                if isinstance(cell, dict):
                    add(cell.get("so_number"))
    return found


def find_so_number_conflicts(
    conn: sqlite3.Connection,
    so_numbers: list[str],
    *,
    exclude_run_id: int | None = None,
) -> list[dict[str, Any]]:
    """Return index rows that already claim any of these SO numbers."""
    ensure_schema(conn)
    conflicts: list[dict[str, Any]] = []
    for so_n in so_numbers:
        key = normalize_so_number(so_n)
        if not key:
            continue
        row = conn.execute(
            """
            SELECT i.so_number, i.run_id, i.user_id, i.filled_order_id, i.created_at,
                   r.distributor_name, r.category, r.season, r.fo_source_filename,
                   r.so_source_filename
            FROM fo_so_match_so_index i
            LEFT JOIN fo_so_match_runs r ON r.id = i.run_id
            WHERE UPPER(i.so_number) = UPPER(?)
            """,
            (key,),
        ).fetchone()
        if not row:
            continue
        run_id = int(row[1])
        if exclude_run_id is not None and run_id == int(exclude_run_id):
            continue
        conflicts.append(
            {
                "so_number": row[0],
                "run_id": run_id,
                "user_id": row[2],
                "filled_order_id": row[3],
                "created_at": row[4],
                "distributor_name": row[5],
                "category": row[6],
                "season": row[7],
                "fo_source_filename": row[8],
                "so_source_filename": row[9],
            }
        )
    return conflicts


def strip_so_numbers_from_run(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    user_id: int,
    so_numbers: list[str],
) -> dict[str, Any]:
    """Remove specific SO# from a match run + free the global SO index.

    Used to repair accidental cross-category locks (e.g. towel SO auto-matched
    onto a Bed FO because buyer matched).
    """
    ensure_schema(conn)
    run = get_match_run(conn, int(run_id), user_id=int(user_id))
    if not run:
        raise ValueError("Match run not found")

    want = {
        (normalize_so_number(n) or "").upper()
        for n in so_numbers
        if normalize_so_number(n)
    }
    if not want:
        raise ValueError("No valid SO numbers to strip")

    def keep_so(raw: Any) -> bool:
        key = (normalize_so_number(raw) or "").upper()
        return bool(key) and key not in want

    detail = run.get("so_line_detail") or []
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except Exception:
            detail = []
    if not isinstance(detail, list):
        detail = []
    new_detail = [
        row for row in detail
        if isinstance(row, dict) and keep_so(row.get("so_number"))
    ]

    rows = run.get("rows") or []
    if isinstance(rows, str):
        try:
            rows = json.loads(rows)
        except Exception:
            rows = []
    if not isinstance(rows, list):
        rows = []
    new_rows: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        nums = [n for n in (r.get("so_numbers") or []) if keep_so(n)]
        breakdown = [
            c for c in (r.get("so_breakdown") or [])
            if isinstance(c, dict) and keep_so(c.get("so_number"))
        ]
        # Drop FO match rows that only existed for stripped SOs and have no leftover.
        if (r.get("so_numbers") or r.get("so_breakdown")) and not nums and not breakdown:
            # Keep FO-only missing rows; drop pure-SO extras tied to stripped numbers.
            status = str(r.get("status") or r.get("match_status") or "").upper()
            if "EXTRA" in status or not (r.get("fo_qty") or r.get("fo_design")):
                continue
        r2 = dict(r)
        if "so_numbers" in r2:
            r2["so_numbers"] = nums
        if "so_breakdown" in r2:
            r2["so_breakdown"] = breakdown
        new_rows.append(r2)

    so_qty = 0.0
    so_net = 0.0
    for row in new_detail:
        try:
            so_qty += float(row.get("qty") or 0)
        except (TypeError, ValueError):
            pass
        try:
            so_net += float(row.get("net") or row.get("net_amount") or 0)
        except (TypeError, ValueError):
            pass

    fo_qty = float(run.get("fo_qty") or 0)
    fo_exmill = float(run.get("fo_exmill_value") or 0)
    conn.execute(
        """
        UPDATE fo_so_match_runs
        SET so_line_detail_json = ?,
            rows_json = ?,
            so_qty = ?,
            so_net_amount = ?,
            delta_qty = ?,
            delta_value = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            json.dumps(new_detail, default=str),
            json.dumps(new_rows, default=str),
            so_qty,
            so_net,
            fo_qty - so_qty,
            fo_exmill - so_net,
            int(run_id),
            int(user_id),
        ),
    )
    for key in want:
        conn.execute(
            "DELETE FROM fo_so_match_so_index WHERE UPPER(so_number) = UPPER(?) AND run_id = ?",
            (key, int(run_id)),
        )
    conn.commit()
    stripped = sorted(want)
    return {
        "run_id": int(run_id),
        "stripped_so_numbers": stripped,
        "run": get_match_run(conn, int(run_id), user_id=int(user_id)),
    }


def _clear_so_index_for_run(conn: sqlite3.Connection, run_id: int) -> None:
    conn.execute("DELETE FROM fo_so_match_so_index WHERE run_id = ?", (int(run_id),))


def _insert_so_index_for_run(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    user_id: int,
    filled_order_id: Any,
    so_numbers: list[str],
) -> None:
    now = _now()
    for so_n in so_numbers:
        key = normalize_so_number(so_n)
        if not key:
            continue
        conn.execute(
            """
            INSERT INTO fo_so_match_so_index
                (so_number, run_id, user_id, filled_order_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (key, int(run_id), int(user_id), filled_order_id, now),
        )


def _cleanup_duplicate_runs_by_filled_order(conn: sqlite3.Connection) -> int:
    """Keep only the latest match run per (user_id, filled_order_id).

    Scoped by user_id: one BD user's re-upload must never delete another
    user's saved run for the same FO id.
    """
    try:
        stale = conn.execute(
            """
            SELECT id FROM fo_so_match_runs
            WHERE filled_order_id IS NOT NULL
              AND id NOT IN (
                SELECT MAX(id) FROM fo_so_match_runs
                WHERE filled_order_id IS NOT NULL
                GROUP BY user_id, filled_order_id
              )
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return 0
    deleted = 0
    for (run_id,) in stale:
        _clear_so_index_for_run(conn, int(run_id))
        conn.execute("DELETE FROM fo_so_match_runs WHERE id = ?", (int(run_id),))
        deleted += 1
    if deleted:
        conn.commit()
    return deleted


def _rebuild_so_index_if_empty(conn: sqlite3.Connection) -> None:
    try:
        count = conn.execute("SELECT COUNT(*) FROM fo_so_match_so_index").fetchone()[0]
    except sqlite3.OperationalError:
        return
    if int(count or 0) > 0:
        return
    # Prefer newest run when the same SO appears in multiple historical rows.
    rows = conn.execute(
        "SELECT id, user_id, filled_order_id, rows_json, so_line_detail_json "
        "FROM fo_so_match_runs ORDER BY id DESC"
    ).fetchall()
    claimed: set[str] = set()
    now = _now()
    for row in rows:
        run = {
            "id": row[0],
            "user_id": row[1],
            "filled_order_id": row[2],
            "rows_json": row[3],
            "so_line_detail": row[4],
        }
        for so_n in extract_so_numbers_from_run_row(run):
            key = (normalize_so_number(so_n) or "").upper()
            if not key or key in claimed:
                continue
            claimed.add(key)
            conn.execute(
                """
                INSERT OR IGNORE INTO fo_so_match_so_index
                    (so_number, run_id, user_id, filled_order_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (normalize_so_number(so_n), int(row[0]), row[1], row[2], now),
            )
    conn.commit()


def save_match_run(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    match_payload: dict[str, Any],
    so_buyer_label: str | None = None,
    so_source_filename: str | None = None,
    so_line_detail: list[Any] | None = None,
    so_pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert a match run from run_match_saved_fo_vs_so_pack result.

    Each Sales Order number may exist in only one saved run. Re-uploading the
    same SO Pack raises DuplicateSalesOrderError (HTTP 409 from the route).
    """
    ensure_schema(conn)
    fo = match_payload.get("fo") or {}
    match = match_payload.get("match") or {}
    totals = match.get("totals") or {}
    counts = match.get("counts") or {}
    rows = match.get("rows") or []
    line_detail_json = (
        json.dumps(so_line_detail, default=str)
        if so_line_detail
        else None
    )

    so_numbers = extract_so_numbers_from_pack(so_pack) if so_pack else []
    if not so_numbers and so_line_detail:
        so_numbers = extract_so_numbers_from_pack({"line_detail": so_line_detail})
    if not so_numbers:
        # Fall back to match row SO lists (still unique per pack).
        so_numbers = extract_so_numbers_from_run_row({"rows": rows})

    conflicts = find_so_number_conflicts(conn, so_numbers)
    if conflicts:
        raise DuplicateSalesOrderError(conflicts)

    mismatch = int(counts.get("QTY_MISMATCH") or 0) + int(counts.get("VALUE_MISMATCH") or 0)
    conn.execute(
        """INSERT INTO fo_so_match_runs (
            user_id, filled_order_id, distributor_id, distributor_name,
            category, season, fo_source_filename, so_buyer_label, so_source_filename,
            fo_qty, so_qty, delta_qty, fo_exmill_value, so_net_amount, delta_value,
            match_count, fuzzy_count, mismatch_count, missing_count, extra_count,
            rows_json, so_line_detail_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            fo.get("id"),
            fo.get("distributor_id"),
            fo.get("distributor_name_raw") or so_buyer_label,
            fo.get("category"),
            fo.get("season"),
            fo.get("source_filename"),
            so_buyer_label,
            so_source_filename,
            totals.get("fo_qty"),
            totals.get("so_qty"),
            totals.get("delta_qty"),
            totals.get("fo_exmill_value"),
            totals.get("so_net_amount"),
            totals.get("delta_value"),
            int(counts.get("MATCH") or 0),
            int(counts.get("MATCH_FUZZY_BRAND") or 0),
            mismatch,
            int(counts.get("MISSING_ON_SO") or 0),
            int(counts.get("EXTRA_ON_SO") or 0),
            json.dumps(rows, default=str),
            line_detail_json,
            _now(),
        ),
    )
    run_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    try:
        _insert_so_index_for_run(
            conn,
            run_id=run_id,
            user_id=user_id,
            filled_order_id=fo.get("id"),
            so_numbers=so_numbers,
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        # Race: another save claimed an SO between check and insert.
        raise DuplicateSalesOrderError(
            find_so_number_conflicts(conn, so_numbers) or [{"so_number": "unknown"}]
        ) from exc
    return get_match_run(conn, run_id, user_id=user_id)


def so_numbers_for_run(
    conn: sqlite3.Connection,
    run_id: int,
    user_id: int | None = None,
) -> list[str]:
    """Every Sales Order number saved inside one match run."""
    run = get_match_run(conn, int(run_id), user_id=user_id)
    if not run:
        return []
    numbers = extract_so_numbers_from_run_row(run)
    for row in conn.execute(
        "SELECT so_number FROM fo_so_match_so_index WHERE run_id = ?",
        (int(run_id),),
    ).fetchall():
        n = normalize_so_number(row[0])
        if n and n.upper() not in {x.upper() for x in numbers}:
            numbers.append(n)
    return numbers


def update_run_from_match(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    user_id: int,
    match_payload: dict[str, Any],
    so_line_detail: list[Any] | None,
    so_pack: dict[str, Any] | None = None,
    so_source_filename: str | None = None,
) -> dict[str, Any] | None:
    """Rewrite one existing run in place from a fresh match result.

    Keeps the run id stable (clients hold it) and re-claims exactly the SO
    numbers that survive, so the global SO index never keeps a stale claim.
    """
    ensure_schema(conn)
    if not get_match_run(conn, int(run_id), user_id=int(user_id)):
        raise ValueError("Match run not found")

    match = match_payload.get("match") or {}
    totals = match.get("totals") or {}
    counts = match.get("counts") or {}
    rows = match.get("rows") or []
    mismatch = int(counts.get("QTY_MISMATCH") or 0) + int(counts.get("VALUE_MISMATCH") or 0)

    so_numbers = extract_so_numbers_from_pack(so_pack) if so_pack else []
    if not so_numbers and so_line_detail:
        so_numbers = extract_so_numbers_from_pack({"line_detail": so_line_detail})
    if not so_numbers:
        so_numbers = extract_so_numbers_from_run_row({"rows": rows})

    conflicts = find_so_number_conflicts(conn, so_numbers, exclude_run_id=int(run_id))
    if conflicts:
        raise DuplicateSalesOrderError(conflicts)

    conn.execute(
        """
        UPDATE fo_so_match_runs
        SET fo_qty = ?, so_qty = ?, delta_qty = ?,
            fo_exmill_value = ?, so_net_amount = ?, delta_value = ?,
            match_count = ?, fuzzy_count = ?, mismatch_count = ?,
            missing_count = ?, extra_count = ?,
            rows_json = ?, so_line_detail_json = ?,
            so_source_filename = COALESCE(?, so_source_filename)
        WHERE id = ? AND user_id = ?
        """,
        (
            totals.get("fo_qty"),
            totals.get("so_qty"),
            totals.get("delta_qty"),
            totals.get("fo_exmill_value"),
            totals.get("so_net_amount"),
            totals.get("delta_value"),
            int(counts.get("MATCH") or 0),
            int(counts.get("MATCH_FUZZY_BRAND") or 0),
            mismatch,
            int(counts.get("MISSING_ON_SO") or 0),
            int(counts.get("EXTRA_ON_SO") or 0),
            json.dumps(rows, default=str),
            json.dumps(so_line_detail, default=str) if so_line_detail else None,
            so_source_filename,
            int(run_id),
            int(user_id),
        ),
    )
    _clear_so_index_for_run(conn, int(run_id))
    row = conn.execute(
        "SELECT filled_order_id FROM fo_so_match_runs WHERE id = ?", (int(run_id),)
    ).fetchone()
    _insert_so_index_for_run(
        conn,
        run_id=int(run_id),
        user_id=int(user_id),
        filled_order_id=row[0] if row else None,
        so_numbers=so_numbers,
    )
    conn.commit()
    return get_match_run(conn, int(run_id), user_id=int(user_id))


def get_match_run(
    conn: sqlite3.Connection,
    run_id: int,
    user_id: int | None = None,
) -> dict[str, Any] | None:
    """Load one match run. If user_id is set, ownership is enforced (desktop).
    Mobile/BD reads pass user_id=None so any auth'd user can open a shared run.
    """
    ensure_schema(conn)
    cols = ", ".join(RUN_COLUMNS)
    if user_id is None:
        row = conn.execute(
            f"SELECT {cols} FROM fo_so_match_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT {cols} FROM fo_so_match_runs WHERE id = ? AND user_id = ?",
            (run_id, user_id),
        ).fetchone()
    if not row:
        return None
    data = _row_to_dict(row, RUN_COLUMNS)
    try:
        data["rows"] = json.loads(data.pop("rows_json") or "[]")
    except Exception:
        data["rows"] = []
        data.pop("rows_json", None)
    so_line_detail_raw = data.pop("so_line_detail_json", None)
    so_line_detail: list[Any] = []
    if isinstance(so_line_detail_raw, str) and so_line_detail_raw.strip():
        try:
            loaded = json.loads(so_line_detail_raw)
            if isinstance(loaded, list):
                so_line_detail = loaded
        except Exception:
            so_line_detail = []
    data["so_line_detail"] = so_line_detail

    # Normalize SO number lists on each row for mobile / desktop clients.
    rows = data.get("rows") or []
    if isinstance(rows, list):
        for r in rows:
            if not isinstance(r, dict):
                continue
            nums = r.get("so_numbers")
            if isinstance(nums, list):
                r["so_numbers"] = [
                    str(n).strip()
                    for n in nums
                    if n is not None and str(n).strip()
                ]
            breakdown = r.get("so_breakdown")
            if isinstance(breakdown, list):
                cleaned = []
                for cell in breakdown:
                    if not isinstance(cell, dict):
                        continue
                    so_n = str(cell.get("so_number") or "").strip()
                    if not so_n:
                        continue
                    cleaned.append(
                        {
                            "so_number": so_n,
                            "qty": float(cell.get("qty") or 0),
                            "net": float(cell.get("net") or 0),
                            "gst": float(cell.get("gst") or 0),
                            "total": float(cell.get("total") or 0),
                        }
                    )
                r["so_breakdown"] = cleaned
            elif r.get("so_numbers"):
                # Legacy match rows: split line SO qty/net evenly across listed SOs.
                split_n = max(1, len(r["so_numbers"]))
                so_qty = float(r.get("so_qty") or 0) / split_n
                so_net = float(r.get("so_net_amount") or 0) / split_n
                r["so_breakdown"] = [
                    {
                        "so_number": so_n,
                        "qty": round(so_qty, 3),
                        "net": round(so_net, 2),
                        "gst": 0.0,
                        "total": round(so_net, 2),
                    }
                    for so_n in r["so_numbers"]
                ]
    data["rows"] = rows
    data["so_totals"] = _compute_so_totals_from_rows(rows if isinstance(rows, list) else [])
    return data


def _compute_so_totals_from_rows(rows: list[Any]) -> dict[str, dict[str, float]]:
    so_totals: dict[str, dict[str, float]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        breakdown = r.get("so_breakdown") or []
        if not isinstance(breakdown, list):
            continue
        for cell in breakdown:
            if not isinstance(cell, dict):
                continue
            so_n = str(cell.get("so_number") or "").strip()
            if not so_n:
                continue
            acc = so_totals.setdefault(
                so_n, {"qty": 0.0, "net": 0.0, "gst": 0.0, "total": 0.0, "exmill": 0.0}
            )
            acc["qty"] = round(acc["qty"] + float(cell.get("qty") or 0), 3)
            acc["net"] = round(acc["net"] + float(cell.get("net") or 0), 2)
            acc["gst"] = round(acc["gst"] + float(cell.get("gst") or 0), 2)
            acc["total"] = round(acc["total"] + float(cell.get("total") or 0), 2)
        fo_ex = float(r.get("fo_exmill_value") or 0)
        so_line_qty = sum(float(c.get("qty") or 0) for c in breakdown if isinstance(c, dict))
        if fo_ex and breakdown:
            for cell in breakdown:
                if not isinstance(cell, dict):
                    continue
                so_n = str(cell.get("so_number") or "").strip()
                if not so_n or so_n not in so_totals:
                    continue
                share = (
                    float(cell.get("qty") or 0) / so_line_qty
                    if so_line_qty > 1e-12
                    else (1.0 / max(1, len(breakdown)))
                )
                so_totals[so_n]["exmill"] = round(
                    so_totals[so_n]["exmill"] + fo_ex * share, 2
                )
    return so_totals


def so_bill_total_from_match_rows(
    rows_json: str | bytes | None,
    so_net_fallback: float | None = None,
) -> float:
    """Final SO bill incl. GST from stored match rows; net-only fallback."""
    rows: list[Any] = []
    if rows_json:
        try:
            parsed = (
                json.loads(rows_json)
                if isinstance(rows_json, (str, bytes))
                else rows_json
            )
            if isinstance(parsed, list):
                rows = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            rows = []
    bill = 0.0
    found = False
    for r in rows:
        if not isinstance(r, dict):
            continue
        breakdown = r.get("so_breakdown")
        if isinstance(breakdown, list) and breakdown:
            for cell in breakdown:
                if not isinstance(cell, dict):
                    continue
                cell_total = float(cell.get("total") or 0)
                if cell_total > 0:
                    bill += cell_total
                    found = True
                else:
                    net = float(cell.get("net") or 0)
                    gst = float(cell.get("gst") or 0)
                    if net or gst:
                        bill += net + gst
                        found = True
            continue
        line_net = float(r.get("so_net_amount") or 0)
        if line_net:
            bill += line_net
            found = True
    if found and bill > 0:
        return round(bill, 2)
    return round(float(so_net_fallback or 0), 2)


def so_net_and_bill_from_match_rows(
    rows_json: str | bytes | None,
    so_net_fallback: float | None = None,
) -> tuple[float, float]:
    """Return (so_net_total, so_bill_total_incl_gst) from stored match rows."""
    rows: list[Any] = []
    if rows_json:
        try:
            parsed = (
                json.loads(rows_json)
                if isinstance(rows_json, (str, bytes))
                else rows_json
            )
            if isinstance(parsed, list):
                rows = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            rows = []
    net_sum = 0.0
    bill_sum = 0.0
    found = False
    for r in rows:
        if not isinstance(r, dict):
            continue
        breakdown = r.get("so_breakdown")
        if isinstance(breakdown, list) and breakdown:
            for cell in breakdown:
                if not isinstance(cell, dict):
                    continue
                net = float(cell.get("net") or 0)
                gst = float(cell.get("gst") or 0)
                total = float(cell.get("total") or 0)
                if total > 0:
                    bill_sum += total
                    net_sum += net if net else total
                    found = True
                elif net or gst:
                    bill_sum += net + gst
                    net_sum += net
                    found = True
            continue
        line_net = float(r.get("so_net_amount") or 0)
        if line_net:
            bill_sum += line_net
            net_sum += line_net
            found = True
    if found and bill_sum > 0:
        return (round(net_sum, 2), round(bill_sum, 2))
    fallback = round(float(so_net_fallback or 0), 2)
    return (fallback, fallback)


def list_match_runs(
    conn: sqlite3.Connection,
    user_id: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """List match runs. user_id=None → all runs (shared with BD app / team)."""
    ensure_schema(conn)
    cols = ", ".join(
        c for c in RUN_COLUMNS if c not in ("rows_json", "so_line_detail_json")
    )
    if user_id is None:
        rows = conn.execute(
            f"""SELECT {cols} FROM fo_so_match_runs
                ORDER BY id DESC
                LIMIT ?""",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT {cols} FROM fo_so_match_runs
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?""",
            (user_id, limit),
        ).fetchall()
    keys = [c for c in RUN_COLUMNS if c not in ("rows_json", "so_line_detail_json")]
    return [_row_to_dict(r, keys) for r in rows]


def delete_match_run(conn: sqlite3.Connection, user_id: int, run_id: int) -> bool:
    ensure_schema(conn)
    cur = conn.execute(
        "DELETE FROM fo_so_match_runs WHERE id = ? AND user_id = ?",
        (run_id, user_id),
    )
    if cur.rowcount > 0:
        _clear_so_index_for_run(conn, run_id)
        conn.commit()
        return True
    conn.commit()
    return False


def lookup_so_in_order_match(
    conn: sqlite3.Connection,
    so_number: str,
    *,
    user_id: int | None = None,
) -> dict[str, Any] | None:
    """Find an SO that exists in FO↔SO Order Match (may have no lifecycle PDF yet)."""
    ensure_schema(conn)
    key = normalize_so_number(so_number)
    if not key:
        return None
    row = conn.execute(
        "SELECT so_number, run_id, user_id, filled_order_id "
        "FROM fo_so_match_so_index WHERE UPPER(so_number) = UPPER(?)",
        (key,),
    ).fetchone()
    if not row:
        return None
    run_id = int(row[1])
    idx_user = int(row[2]) if row[2] is not None else None
    if user_id is not None and idx_user is not None and idx_user != int(user_id):
        return None
    run = get_match_run(conn, run_id, user_id=user_id)
    if run is None and user_id is not None:
        run = get_match_run(conn, run_id, user_id=None)
    if not run:
        return None
    want = key.upper()
    lines = [
        dict(l)
        for l in (run.get("so_line_detail") or [])
        if isinstance(l, dict)
        and (normalize_so_number(l.get("so_number")) or "").upper() == want
    ]

    line_qty = 0.0
    line_net = 0.0
    line_qtys: list[float] = []
    for l in lines:
        try:
            q = float(l.get("qty") or l.get("quantity") or 0)
        except (TypeError, ValueError):
            q = 0.0
        if q > 0:
            line_qtys.append(q)
            line_qty += q
        try:
            line_net += float(l.get("net_amount") or l.get("net") or 0)
        except (TypeError, ValueError):
            pass

    so_totals = run.get("so_totals") if isinstance(run.get("so_totals"), dict) else {}
    totals_qty = 0.0
    totals_net = 0.0
    for tkey, tval in so_totals.items():
        if not isinstance(tval, dict):
            continue
        if (normalize_so_number(tkey) or "").upper() != want:
            continue
        try:
            totals_qty = float(tval.get("qty") or 0)
        except (TypeError, ValueError):
            totals_qty = 0.0
        try:
            totals_net = float(tval.get("net") or 0)
        except (TypeError, ValueError):
            totals_net = 0.0
        break

    def _compress_repeated_so_qty(qtys: list[float], bridge: float) -> float:
        """Undo SO-header qty stamped on every design line (16×648, 8×396)."""
        if not qtys:
            return bridge
        total = float(sum(qtys))
        rounded = [round(q, 4) for q in qtys]
        if len(set(rounded)) == 1:
            return float(rounded[0])
        if bridge > 0 and total > bridge * 1.2:
            ratio = total / bridge
            n = int(round(ratio))
            if n >= 2 and abs(ratio - n) < 0.08:
                return bridge
        # Mode: most common line qty, if sum ≈ n × mode
        counts: dict[float, int] = {}
        for q in rounded:
            counts[q] = counts.get(q, 0) + 1
        mode = max(counts.items(), key=lambda kv: kv[1])[0]
        mode_hits = counts[mode]
        if mode > 0 and len(qtys) >= 2 and total > mode * 1.2 and mode_hits >= (len(qtys) + 1) // 2:
            ratio = total / mode
            n = int(round(ratio))
            if n >= 2 and abs(ratio - n) < 0.08:
                return float(mode)
        return total

    qty = _compress_repeated_so_qty(line_qtys, totals_qty)
    if qty <= 0:
        qty = totals_qty
    net = line_net if line_net > 0 else totals_net

    return {
        "so_number": key,
        "run_id": run_id,
        "filled_order_id": run.get("filled_order_id") or row[3],
        "distributor_id": run.get("distributor_id"),
        "distributor_name": run.get("distributor_name"),
        "season": run.get("season"),
        "category": run.get("category"),
        "so_qty": round(qty, 4),
        "so_net": round(net, 2),
        "line_count": len(lines),
        "line_detail": lines,
    }


def sales_order_parsed_from_order_match(lookup: dict[str, Any]) -> dict[str, Any]:
    """Lifecycle-compatible sales_order_parsed built from Order Match SO lines."""
    lines_out: list[dict[str, Any]] = []
    for r in lookup.get("line_detail") or []:
        if not isinstance(r, dict):
            continue
        q = 0.0
        try:
            q = float(r.get("qty") or r.get("quantity") or 0)
        except (TypeError, ValueError):
            q = 0.0
        net = 0.0
        try:
            net = float(r.get("net_amount") or r.get("net") or 0)
        except (TypeError, ValueError):
            net = 0.0
        name = (
            str(r.get("product_name") or "").strip()
            or str(r.get("product_detail") or "").strip()
            or str(r.get("material_code") or "").strip()
            or "Item"
        )
        lines_out.append(
            {
                "item_name": name,
                "product": name,
                "material_code": r.get("material_code"),
                "qty": q,
                "quantity": q,
                "net_amount": net,
                "value": net,
                "amount": net,
                "so_number": lookup.get("so_number"),
            }
        )
    return {
        "source": "order_match",
        "order_match_run_id": lookup.get("run_id"),
        "header": {
            "order_ref_no": lookup.get("so_number"),
            "total_qty": lookup.get("so_qty"),
            "net_amount": lookup.get("so_net"),
        },
        "line_items": lines_out,
        "rows": lines_out,
        "totals": {
            "total_qty": lookup.get("so_qty"),
            "net_amount": lookup.get("so_net"),
        },
    }

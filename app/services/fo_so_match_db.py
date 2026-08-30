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


def fo_has_match_for_so_zip(
    conn: sqlite3.Connection,
    user_id: int,
    filled_order_id: int,
    so_source_filename: str | None,
) -> bool:
    """True if this FO already has a saved match run from the same SO pack file."""
    name = (so_source_filename or "").strip()
    if not name or not filled_order_id:
        return False
    ensure_schema(conn)
    row = conn.execute(
        """
        SELECT id FROM fo_so_match_runs
        WHERE user_id = ? AND filled_order_id = ?
          AND LOWER(COALESCE(so_source_filename, '')) = LOWER(?)
        ORDER BY id DESC LIMIT 1
        """,
        (int(user_id), int(filled_order_id), name),
    ).fetchone()
    return row is not None


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
            "SELECT so_number, run_id, user_id, filled_order_id, created_at "
            "FROM fo_so_match_so_index WHERE UPPER(so_number) = UPPER(?)",
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
            }
        )
    return conflicts


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
    """Keep only the latest match run per filled_order_id (team-wide)."""
    try:
        stale = conn.execute(
            """
            SELECT id FROM fo_so_match_runs
            WHERE filled_order_id IS NOT NULL
              AND id NOT IN (
                SELECT MAX(id) FROM fo_so_match_runs
                WHERE filled_order_id IS NOT NULL
                GROUP BY filled_order_id
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
    try:
        rows = conn.execute(
            "SELECT id, user_id, filled_order_id, rows_json, so_line_detail_json "
            "FROM fo_so_match_runs ORDER BY id DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = conn.execute(
            "SELECT id, user_id, filled_order_id, rows_json "
            "FROM fo_so_match_runs ORDER BY id DESC"
        ).fetchall()
        rows = [(r[0], r[1], r[2], r[3], None) for r in rows]
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
    _mask_fo_fields_if_detached(data)
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


_DEDUPED_SO_NET_SQL = """
    SELECT so_net_amount, rows_json
    FROM fo_so_match_runs
    WHERE user_id = ?
      {date_filter}
      AND id IN (
        SELECT MAX(id)
        FROM fo_so_match_runs
        WHERE user_id = ?
          {inner_date_filter}
        GROUP BY CASE
          WHEN filled_order_id IS NOT NULL
            THEN 'fo:' || CAST(filled_order_id AS TEXT)
          ELSE 'party:' || CAST(COALESCE(distributor_id, 0) AS TEXT)
               || '|' || COALESCE(season, '')
               || '|' || COALESCE(category, '')
        END
      )
"""


def sum_deduped_so_net_for_user(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> float:
    """Order Desk SO total (ex-mill / pre-tax) — latest FO↔SO match per slot.

    Same dedupe as Payment Status / Sales Orders tab: one run per filled
    order (or party+season+category when FO id missing). Uses matched SO
    lines from rows_json — not distributor Filled Order ex-mill totals.
    """
    ensure_schema(conn)
    if date_from and date_to:
        date_filter = "AND DATE(created_at) BETWEEN ? AND ?"
        inner_date_filter = "AND DATE(created_at) BETWEEN ? AND ?"
        params = (user_id, date_from, date_to, user_id, date_from, date_to)
    else:
        date_filter = ""
        inner_date_filter = ""
        params = (user_id, user_id)
    sql = _DEDUPED_SO_NET_SQL.format(
        date_filter=date_filter,
        inner_date_filter=inner_date_filter,
    )
    rows = conn.execute(sql, params).fetchall()
    total = 0.0
    for so_net_amount, rows_json in rows:
        net, _ = so_net_and_bill_from_match_rows(rows_json, so_net_amount)
        total += net
    return round(total, 2)


def list_match_runs(
    conn: sqlite3.Connection,
    user_id: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """List match runs. user_id=None → all runs (shared with BD app / team)."""
    ensure_schema(conn)
    if user_id is not None:
        resurrect_so_runs_orphaned_by_fo_delete(conn, user_id)
        repair_stale_detached_match_rows(conn, user_id)
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
    out = [_row_to_dict(r, keys) for r in rows]
    for item in out:
        _mask_fo_fields_if_detached(item)
    return out


def _strip_so_from_run(run: dict[str, Any], so_number: str) -> dict[str, Any] | None:
    """Remove one SO from a match run. Returns None when no SOs remain."""
    key = normalize_so_number(so_number)
    if not key:
        return None
    want = key.upper()

    line_detail = [
        l
        for l in (run.get("so_line_detail") or [])
        if not (
            isinstance(l, dict)
            and (normalize_so_number(l.get("so_number")) or "").upper() == want
        )
    ]

    rows_out: list[dict[str, Any]] = []
    for r in run.get("rows") or []:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        nums = [
            n
            for n in (row.get("so_numbers") or [])
            if (normalize_so_number(n) or "").upper() != want
        ]
        breakdown = row.get("so_breakdown") or []
        new_breakdown = [
            dict(c)
            for c in breakdown
            if isinstance(c, dict)
            and (normalize_so_number(c.get("so_number")) or "").upper() != want
        ]
        if not nums and not new_breakdown:
            if any(
                isinstance(c, dict)
                and (normalize_so_number(c.get("so_number")) or "").upper() == want
                for c in breakdown
            ):
                continue
            if not breakdown and not row.get("so_numbers"):
                rows_out.append(row)
            continue
        row["so_numbers"] = nums
        row["so_breakdown"] = new_breakdown
        if new_breakdown:
            row["so_qty"] = round(sum(float(c.get("qty") or 0) for c in new_breakdown), 4)
            row["so_net_amount"] = round(
                sum(float(c.get("net") or 0) for c in new_breakdown), 2
            )
        rows_out.append(row)

    remaining = extract_so_numbers_from_run_row({"rows": rows_out})
    if not remaining:
        return None

    updated = dict(run)
    updated["so_line_detail"] = line_detail
    updated["rows"] = rows_out
    updated["so_totals"] = _compute_so_totals_from_rows(rows_out)
    fo_qty = so_qty = fo_ex = so_net = 0.0
    match_count = fuzzy_count = mismatch_count = missing_count = extra_count = 0
    for r in rows_out:
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
    updated.update(
        {
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
    )
    return updated


def delete_match_so_from_run(
    conn: sqlite3.Connection,
    user_id: int,
    run_id: int,
    so_number: str,
) -> dict[str, Any] | None:
    """Delete one SO from a match run. Returns {deleted_run: bool} or None if not found."""
    ensure_schema(conn)
    run = get_match_run(conn, run_id, user_id=user_id)
    if not run:
        return None
    key = normalize_so_number(so_number)
    if not key:
        return None
    updated = _strip_so_from_run(run, key)
    conn.execute(
        "DELETE FROM fo_so_match_so_index WHERE UPPER(so_number) = UPPER(?)",
        (key,),
    )
    if updated is None:
        cur = conn.execute(
            "DELETE FROM fo_so_match_runs WHERE id = ? AND user_id = ?",
            (run_id, user_id),
        )
        if cur.rowcount > 0:
            _clear_so_index_for_run(conn, run_id)
            conn.commit()
            return {"deleted_run": True, "run_id": run_id}
        conn.commit()
        return None
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
            updated["fo_qty"],
            updated["so_qty"],
            updated["delta_qty"],
            updated["fo_exmill_value"],
            updated["so_net_amount"],
            updated["delta_value"],
            updated["match_count"],
            updated["fuzzy_count"],
            updated["mismatch_count"],
            updated["missing_count"],
            updated["extra_count"],
            json.dumps(updated.get("rows") or [], default=str),
            json.dumps(updated.get("so_line_detail") or [], default=str),
            run_id,
            user_id,
        ),
    )
    conn.commit()
    return {"deleted_run": False, "run_id": run_id}


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


def _strip_fo_from_match_row(row: dict[str, Any]) -> None:
    """Remove FO match semantics from one stored row; keep SO breakdown intact."""
    row["fo_qty"] = None
    row["fo_exmill_value"] = None
    row["delta_qty"] = row.get("so_qty")
    row["delta_value"] = row.get("so_net_amount")
    row["status"] = "UNMATCHED"


def _strip_fo_from_match_rows(rows: list[Any]) -> list[Any]:
    out: list[Any] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        _strip_fo_from_match_row(row)
        out.append(row)
    return out


def _rows_json_still_has_fo_match(rows_json: str | bytes | None) -> bool:
    if not rows_json:
        return False
    try:
        parsed = (
            json.loads(rows_json)
            if isinstance(rows_json, (str, bytes))
            else rows_json
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(parsed, list):
        return False
    for r in parsed:
        if not isinstance(r, dict):
            continue
        if r.get("fo_qty") is not None:
            return True
        status = str(r.get("status") or "").upper()
        if status in ("MATCH", "MATCH_FUZZY_BRAND"):
            return True
    return False


def repair_stale_detached_match_rows(conn: sqlite3.Connection, user_id: int) -> int:
    """Persist FO strip on old detached runs whose rows_json still look matched."""
    ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT id, rows_json FROM fo_so_match_runs
        WHERE user_id = ? AND filled_order_id IS NULL
        """,
        (user_id,),
    ).fetchall()
    repaired = 0
    for run_id, rows_json in rows:
        if not _rows_json_still_has_fo_match(rows_json):
            continue
        try:
            parsed = json.loads(rows_json or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(parsed, list):
            continue
        stripped = _strip_fo_from_match_rows(parsed)
        conn.execute(
            """
            UPDATE fo_so_match_runs SET
                rows_json = ?,
                match_count = 0,
                fuzzy_count = 0,
                mismatch_count = 0,
                missing_count = 0,
                extra_count = 0
            WHERE id = ? AND user_id = ?
            """,
            (json.dumps(stripped, default=str), run_id, user_id),
        )
        repaired += 1
    if repaired:
        conn.commit()
    return repaired


def _mask_fo_fields_if_detached(data: dict[str, Any]) -> None:
    """FO gone → keep SO pack, drop FO match numbers so UI cannot look matched."""
    if data.get("filled_order_id") is not None:
        return
    data["fo_qty"] = None
    data["fo_exmill_value"] = None
    data["fo_source_filename"] = None
    data["match_count"] = 0
    data["fuzzy_count"] = 0
    data["mismatch_count"] = 0
    data["missing_count"] = 0
    data["extra_count"] = 0
    data["delta_qty"] = data.get("so_qty")
    data["delta_value"] = data.get("so_net_amount")
    rows = data.get("rows")
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict):
                _strip_fo_from_match_row(r)


def detach_match_runs_from_filled_order(
    conn: sqlite3.Connection,
    user_id: int,
    filled_order_id: int,
    *,
    archive: bool = True,
) -> int:
    """FO deleted → unlink match. Sales Order pack stays on Order Desk unmatched."""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT id FROM fo_so_match_runs WHERE user_id = ? AND filled_order_id = ?",
        (user_id, filled_order_id),
    ).fetchall()
    detached = 0
    oda = None
    if archive and rows:
        try:
            from app.services import order_desk_archive as oda_mod

            oda = oda_mod
        except Exception:
            oda = None
    for row in rows:
        run_id = int(row[0])
        run = get_match_run(conn, run_id, user_id=user_id)
        if oda is not None and run:
            try:
                oda.archive_match_run(conn, user_id, run, restore_scope="run")
            except Exception:
                pass
        stripped_rows_json: str | None = None
        if run:
            stripped = _strip_fo_from_match_rows(run.get("rows") or [])
            stripped_rows_json = json.dumps(stripped, default=str)
        conn.execute(
            """
            UPDATE fo_so_match_runs SET
                filled_order_id = NULL,
                fo_qty = NULL,
                fo_exmill_value = NULL,
                fo_source_filename = NULL,
                match_count = 0,
                fuzzy_count = 0,
                mismatch_count = 0,
                missing_count = 0,
                extra_count = 0,
                delta_qty = so_qty,
                delta_value = so_net_amount,
                rows_json = COALESCE(?, rows_json)
            WHERE id = ? AND user_id = ?
            """,
            (stripped_rows_json, run_id, user_id),
        )
        conn.execute(
            "UPDATE fo_so_match_so_index SET filled_order_id = NULL WHERE run_id = ?",
            (run_id,),
        )
        detached += 1
    if detached:
        conn.commit()
    return detached


def rematch_run_against_fo(
    conn: sqlite3.Connection,
    user_id: int,
    run_id: int,
    filled_order_id: int,
) -> bool:
    """Re-run FO vs saved SO pack for an existing match run."""
    import filled_orders_db as fodb
    from app.services.fo_so_match_lab import run_match_saved_fo_vs_so_pack

    ensure_schema(conn)
    run = get_match_run(conn, run_id, user_id=user_id)
    if not run:
        return False
    line_detail = run.get("so_line_detail") or []
    if not line_detail:
        return False
    fo = fodb.get_filled_order(conn, user_id, filled_order_id)
    if not fo:
        return False
    fo_items = fodb.get_filled_order_items(conn, filled_order_id)
    so_pack: dict[str, Any] = {
        "line_detail": line_detail,
        "meta": {
            "source_filename": run.get("so_source_filename"),
            "primary_buyer_name": run.get("so_buyer_label"),
        },
    }
    match_payload = run_match_saved_fo_vs_so_pack(
        fo_meta={**fo, "id": filled_order_id},
        fo_items=fo_items,
        so_pack_payload=so_pack,
    )
    fo_meta = match_payload.get("fo") or {}
    match = match_payload.get("match") or {}
    totals = match.get("totals") or {}
    counts = match.get("counts") or {}
    rows = match.get("rows") or []
    mismatch = int(counts.get("QTY_MISMATCH") or 0) + int(counts.get("VALUE_MISMATCH") or 0)
    conn.execute(
        """
        UPDATE fo_so_match_runs SET
            filled_order_id = ?,
            distributor_id = ?,
            distributor_name = ?,
            category = ?,
            season = ?,
            fo_source_filename = ?,
            fo_qty = ?, so_qty = ?, delta_qty = ?,
            fo_exmill_value = ?, so_net_amount = ?, delta_value = ?,
            match_count = ?, fuzzy_count = ?, mismatch_count = ?,
            missing_count = ?, extra_count = ?,
            rows_json = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            filled_order_id,
            fo_meta.get("distributor_id"),
            fo_meta.get("distributor_name_raw") or run.get("distributor_name"),
            fo_meta.get("category"),
            fo_meta.get("season"),
            fo_meta.get("source_filename"),
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
            run_id,
            user_id,
        ),
    )
    conn.execute(
        "UPDATE fo_so_match_so_index SET filled_order_id = ? WHERE run_id = ?",
        (filled_order_id, run_id),
    )
    return True


def relink_orphan_match_runs_to_filled_order(
    conn: sqlite3.Connection,
    user_id: int,
    filled_order_id: int,
    entity_key: str,
) -> int:
    """Re-attach detached SO match runs when the same FO is uploaded again."""
    ensure_schema(conn)
    key_lower = str(entity_key or "").strip().lower()
    if not key_lower:
        return 0
    try:
        from app.services import order_desk_archive as oda

        fo_entity_key = oda.fo_entity_key
    except Exception:
        return 0

    existing = conn.execute(
        "SELECT id FROM fo_so_match_runs WHERE user_id = ? AND filled_order_id = ? LIMIT 1",
        (user_id, filled_order_id),
    ).fetchone()
    if existing:
        return 0

    rows = conn.execute(
        """
        SELECT r.id, r.distributor_name, r.category, r.season
        FROM fo_so_match_runs r
        LEFT JOIN filled_orders fo
          ON fo.id = r.filled_order_id AND fo.user_id = r.user_id
        WHERE r.user_id = ?
          AND (r.filled_order_id IS NULL OR fo.id IS NULL)
        ORDER BY r.id DESC
        """,
        (user_id,),
    ).fetchall()
    relinked = 0
    for row in rows:
        run_id = int(row[0])
        run_key = fo_entity_key(row[1], row[2], row[3]).strip().lower()
        if run_key != key_lower:
            continue
        if rematch_run_against_fo(conn, user_id, run_id, filled_order_id):
            relinked += 1
    if relinked:
        conn.commit()
    return relinked


def purge_orphan_match_runs(conn: sqlite3.Connection, user_id: int) -> int:
    """Do not drop SO packs when FO is gone — they stay unmatched until FO returns."""
    ensure_schema(conn)
    return 0


def resurrect_so_runs_orphaned_by_fo_delete(
    conn: sqlite3.Connection, user_id: int
) -> int:
    """Bring back SO packs archived when FO delete used to wipe the match row."""
    ensure_schema(conn)
    try:
        from app.services import order_desk_archive as oda

        oda.ensure_schema(conn)
    except Exception:
        return 0
    try:
        rows = conn.execute(
            """
            SELECT payload_json FROM order_desk_archive
            WHERE user_id = ? AND kind = 'match_run' AND restored_at IS NULL
              AND datetime(expires_at) > datetime('now')
            ORDER BY id DESC
            """,
            (user_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return 0
    restored = 0
    for (payload_json,) in rows:
        try:
            payload = json.loads(payload_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        snap = payload.get("run") if isinstance(payload.get("run"), dict) else {}
        if not snap:
            continue
        so_numbers = list(payload.get("so_numbers") or [])
        if not so_numbers:
            so_numbers = extract_so_numbers_from_run_row(snap)
        if not so_numbers:
            continue
        if find_so_number_conflicts(conn, so_numbers):
            continue
        fo_id = snap.get("filled_order_id")
        if fo_id:
            try:
                live_fo = conn.execute(
                    "SELECT id FROM filled_orders WHERE id = ? AND user_id = ?",
                    (int(fo_id), user_id),
                ).fetchone()
            except (TypeError, ValueError, sqlite3.OperationalError):
                live_fo = None
            if live_fo:
                continue
        line_detail = [
            dict(l) for l in (snap.get("so_line_detail") or []) if isinstance(l, dict)
        ]
        rows_json = json.dumps(snap.get("rows") or [], default=str)
        line_json = json.dumps(line_detail, default=str) if line_detail else None
        conn.execute(
            """INSERT INTO fo_so_match_runs (
                user_id, filled_order_id, distributor_id, distributor_name,
                category, season, fo_source_filename, so_buyer_label, so_source_filename,
                fo_qty, so_qty, delta_qty, fo_exmill_value, so_net_amount, delta_value,
                match_count, fuzzy_count, mismatch_count, missing_count, extra_count,
                rows_json, so_line_detail_json, created_at
            ) VALUES (?, NULL, ?, ?, ?, ?, NULL, ?, ?, NULL, ?, ?, NULL, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                snap.get("distributor_id"),
                snap.get("distributor_name"),
                snap.get("category"),
                snap.get("season"),
                snap.get("so_buyer_label"),
                snap.get("so_source_filename"),
                snap.get("so_qty"),
                snap.get("so_qty"),
                snap.get("so_net_amount"),
                snap.get("so_net_amount"),
                int(snap.get("mismatch_count") or 0),
                int(snap.get("missing_count") or 0),
                int(snap.get("extra_count") or 0),
                rows_json,
                line_json,
                snap.get("created_at") or _now(),
            ),
        )
        run_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        try:
            _insert_so_index_for_run(
                conn,
                run_id=run_id,
                user_id=user_id,
                filled_order_id=None,
                so_numbers=so_numbers,
            )
        except sqlite3.IntegrityError:
            conn.execute(
                "DELETE FROM fo_so_match_runs WHERE id = ? AND user_id = ?",
                (run_id, user_id),
            )
            continue
        restored += 1
    if restored:
        conn.commit()
    return restored


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

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

-- SO splits: tracks when a mother SO is split into child SOs.
-- mother_run_id = the fo_so_match_runs.id that was split FROM
-- child_run_id  = the fo_so_match_runs.id created for the child SO (nullable until child is matched)
CREATE TABLE IF NOT EXISTS so_splits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    mother_run_id INTEGER NOT NULL,
    child_run_id INTEGER,
    mother_so_numbers TEXT,
    child_so_numbers TEXT,
    distributor_id INTEGER,
    season TEXT,
    category TEXT,
    split_articles_json TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (mother_run_id) REFERENCES fo_so_match_runs(id),
    FOREIGN KEY (child_run_id) REFERENCES fo_so_match_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_so_splits_user
    ON so_splits(user_id, mother_run_id);
CREATE INDEX IF NOT EXISTS idx_so_splits_child
    ON so_splits(user_id, child_run_id);
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


def ensure_schema(conn: sqlite3.Connection) -> None:
    global _schema_ensured
    if _schema_ensured:
        try:
            conn.execute("SELECT 1 FROM fo_so_match_runs LIMIT 1")
            conn.execute("SELECT so_line_detail_json FROM fo_so_match_runs LIMIT 1")
            conn.execute("SELECT 1 FROM fo_so_match_so_index LIMIT 1")
            conn.execute("SELECT 1 FROM so_splits LIMIT 1")
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


# ── SO Split helpers ──────────────────────────────────────────────


def create_so_split(
    conn: sqlite3.Connection,
    user_id: int,
    mother_run_id: int,
    child_so_numbers: list[str],
    split_articles: list[dict[str, Any]],
    note: str | None = None,
) -> dict[str, Any]:
    """Split a mother SO: reduce mother qty, record the split.

    `split_articles` = list of {article_code, article_name, split_qty, split_net, split_gst, split_total}
    representing what moves from mother → child.

    Returns the created split record + updated mother rows_json.
    """
    ensure_schema(conn)

    # Fetch mother run
    row = conn.execute(
        "SELECT * FROM fo_so_match_runs WHERE id = ? AND user_id = ?",
        (mother_run_id, user_id),
    ).fetchone()
    if not row:
        raise ValueError("Mother SO run not found")

    mother = _row_to_dict(row, RUN_COLUMNS)
    mother_rows: list[dict] = json.loads(mother["rows_json"] or "[]")

    # Build lookup of what to split
    split_lookup: dict[str, dict] = {}
    for art in split_articles:
        code = str(art.get("article_code") or "").strip().upper()
        if code:
            split_lookup[code] = art

    # Reduce mother SO rows by split quantities
    updated_mother_rows = []
    for mrow in mother_rows:
        art_code = str(
            mrow.get("article_code") or mrow.get("fo_article_code") or ""
        ).strip().upper()
        sp = split_lookup.get(art_code)
        if sp:
            split_qty = float(sp.get("split_qty") or 0)
            # Reduce SO qty on mother
            old_so_qty = float(mrow.get("so_qty") or 0)
            new_so_qty = max(0.0, old_so_qty - split_qty)
            mrow["so_qty"] = new_so_qty
            # Reduce in so_breakdown too
            remaining = split_qty
            for bd in (mrow.get("so_breakdown") or []):
                if remaining <= 0:
                    break
                bd_qty = float(bd.get("qty") or 0)
                reduce = min(bd_qty, remaining)
                if bd_qty > 0:
                    ratio = reduce / bd_qty
                    bd["qty"] = round(bd_qty - reduce, 4)
                    bd["net"] = round(float(bd.get("net") or 0) * (1 - ratio), 2)
                    bd["gst"] = round(float(bd.get("gst") or 0) * (1 - ratio), 2)
                    bd["total"] = round(float(bd.get("total") or 0) * (1 - ratio), 2)
                remaining -= reduce
            # Recalculate line-level so_net_amount
            mrow["so_net_amount"] = sum(
                float(bd.get("net") or 0) for bd in (mrow.get("so_breakdown") or [])
            )
            # Update match status
            fo_qty = float(mrow.get("fo_qty") or 0)
            if new_so_qty == 0 and fo_qty > 0:
                mrow["status"] = "MISSING_ON_SO"
            elif new_so_qty > 0 and abs(new_so_qty - fo_qty) > 0.01:
                mrow["status"] = "QTY_MISMATCH"
        updated_mother_rows.append(mrow)

    # Recalculate mother run aggregates
    new_so_qty_total = sum(float(r.get("so_qty") or 0) for r in updated_mother_rows)
    new_so_net_total = sum(float(r.get("so_net_amount") or 0) for r in updated_mother_rows)
    fo_qty_total = float(mother.get("fo_qty") or 0)

    # Update mother run
    conn.execute(
        """UPDATE fo_so_match_runs
           SET rows_json = ?, so_qty = ?, so_net_amount = ?, delta_qty = ?, delta_value = ?
           WHERE id = ? AND user_id = ?""",
        (
            json.dumps(updated_mother_rows, default=str),
            new_so_qty_total,
            new_so_net_total,
            fo_qty_total - new_so_qty_total,
            float(mother.get("fo_exmill_value") or 0) - new_so_net_total,
            mother_run_id,
            user_id,
        ),
    )

    # Extract mother SO numbers
    mother_so_numbers = set()
    for mrow in mother_rows:
        for sn in (mrow.get("so_numbers") or []):
            mother_so_numbers.add(sn)

    # Create split record
    cur = conn.execute(
        """INSERT INTO so_splits
           (user_id, mother_run_id, mother_so_numbers, child_so_numbers,
            distributor_id, season, category, split_articles_json, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            mother_run_id,
            json.dumps(sorted(mother_so_numbers)),
            json.dumps(child_so_numbers),
            mother.get("distributor_id"),
            mother.get("season"),
            mother.get("category"),
            json.dumps(split_articles, default=str),
            note,
        ),
    )
    split_id = cur.lastrowid
    conn.commit()

    return {
        "split_id": split_id,
        "mother_run_id": mother_run_id,
        "child_so_numbers": child_so_numbers,
        "split_articles": split_articles,
        "mother_so_qty_after": new_so_qty_total,
        "mother_so_net_after": new_so_net_total,
    }


def link_child_run_to_split(
    conn: sqlite3.Connection,
    user_id: int,
    split_id: int,
    child_run_id: int,
) -> bool:
    """After child SO is matched as its own run, link it back to the split."""
    ensure_schema(conn)
    cur = conn.execute(
        "UPDATE so_splits SET child_run_id = ? WHERE id = ? AND user_id = ?",
        (child_run_id, split_id, user_id),
    )
    conn.commit()
    return cur.rowcount > 0


def list_splits_for_run(
    conn: sqlite3.Connection,
    user_id: int,
    mother_run_id: int,
) -> list[dict[str, Any]]:
    """List all splits created from a mother run."""
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, mother_run_id, child_run_id, mother_so_numbers, child_so_numbers,
                  distributor_id, season, category, split_articles_json, note, created_at
           FROM so_splits WHERE user_id = ? AND mother_run_id = ?
           ORDER BY created_at""",
        (user_id, mother_run_id),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["split_articles"] = json.loads(d.pop("split_articles_json", "[]"))
        d["mother_so_numbers"] = json.loads(d.get("mother_so_numbers") or "[]")
        d["child_so_numbers"] = json.loads(d.get("child_so_numbers") or "[]")
        result.append(d)
    return result


def list_all_splits(
    conn: sqlite3.Connection,
    user_id: int,
) -> list[dict[str, Any]]:
    """List all SO splits for this user."""
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT s.id, s.mother_run_id, s.child_run_id,
                  s.mother_so_numbers, s.child_so_numbers,
                  s.distributor_id, s.season, s.category,
                  s.split_articles_json, s.note, s.created_at,
                  m.distributor_name
           FROM so_splits s
           LEFT JOIN fo_so_match_runs m ON m.id = s.mother_run_id
           WHERE s.user_id = ?
           ORDER BY s.created_at DESC""",
        (user_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["split_articles"] = json.loads(d.pop("split_articles_json", "[]"))
        d["mother_so_numbers"] = json.loads(d.get("mother_so_numbers") or "[]")
        d["child_so_numbers"] = json.loads(d.get("child_so_numbers") or "[]")
        result.append(d)
    return result


def undo_so_split(
    conn: sqlite3.Connection,
    user_id: int,
    split_id: int,
) -> bool:
    """Undo a split: restore mother SO quantities, delete split record.
    Does NOT delete the child run (if any) — that's a separate action."""
    ensure_schema(conn)

    split_row = conn.execute(
        "SELECT * FROM so_splits WHERE id = ? AND user_id = ?",
        (split_id, user_id),
    ).fetchone()
    if not split_row:
        return False

    split = dict(split_row)
    mother_run_id = split["mother_run_id"]
    split_articles: list[dict] = json.loads(split.get("split_articles_json") or "[]")

    # Fetch mother run
    mother_row = conn.execute(
        "SELECT * FROM fo_so_match_runs WHERE id = ? AND user_id = ?",
        (mother_run_id, user_id),
    ).fetchone()
    if not mother_row:
        # Mother run deleted — just remove split record
        conn.execute("DELETE FROM so_splits WHERE id = ?", (split_id,))
        conn.commit()
        return True

    mother = _row_to_dict(mother_row, RUN_COLUMNS)
    mother_rows: list[dict] = json.loads(mother["rows_json"] or "[]")

    # Build restore lookup
    restore_lookup: dict[str, dict] = {}
    for art in split_articles:
        code = str(art.get("article_code") or "").strip().upper()
        if code:
            restore_lookup[code] = art

    # Add back split quantities to mother
    for mrow in mother_rows:
        art_code = str(
            mrow.get("article_code") or mrow.get("fo_article_code") or ""
        ).strip().upper()
        sp = restore_lookup.get(art_code)
        if sp:
            restore_qty = float(sp.get("split_qty") or 0)
            mrow["so_qty"] = float(mrow.get("so_qty") or 0) + restore_qty
            # Restore in first so_breakdown entry proportionally
            bds = mrow.get("so_breakdown") or []
            if bds:
                bd = bds[0]
                bd["qty"] = float(bd.get("qty") or 0) + restore_qty
                restore_net = float(sp.get("split_net") or 0)
                restore_gst = float(sp.get("split_gst") or 0)
                restore_total = float(sp.get("split_total") or 0)
                bd["net"] = round(float(bd.get("net") or 0) + restore_net, 2)
                bd["gst"] = round(float(bd.get("gst") or 0) + restore_gst, 2)
                bd["total"] = round(float(bd.get("total") or 0) + restore_total, 2)
            mrow["so_net_amount"] = sum(
                float(b.get("net") or 0) for b in bds
            )
            # Restore match status
            fo_qty = float(mrow.get("fo_qty") or 0)
            if abs(mrow["so_qty"] - fo_qty) <= 0.01:
                mrow["status"] = "MATCH"

    new_so_qty = sum(float(r.get("so_qty") or 0) for r in mother_rows)
    new_so_net = sum(float(r.get("so_net_amount") or 0) for r in mother_rows)

    conn.execute(
        """UPDATE fo_so_match_runs
           SET rows_json = ?, so_qty = ?, so_net_amount = ?,
               delta_qty = ?, delta_value = ?
           WHERE id = ? AND user_id = ?""",
        (
            json.dumps(mother_rows, default=str),
            new_so_qty,
            new_so_net,
            float(mother.get("fo_qty") or 0) - new_so_qty,
            float(mother.get("fo_exmill_value") or 0) - new_so_net,
            mother_run_id,
            user_id,
        ),
    )
    conn.execute("DELETE FROM so_splits WHERE id = ?", (split_id,))
    conn.commit()
    return True


def get_mother_candidates(
    conn: sqlite3.Connection,
    user_id: int,
    distributor_id: int,
    season: str,
    category: str,
) -> list[dict[str, Any]]:
    """Find existing match runs for this distributor+season+category
    that could be the mother SO for a split."""
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, distributor_name, so_source_filename, so_qty, so_net_amount,
                  fo_qty, fo_exmill_value, rows_json, created_at
           FROM fo_so_match_runs
           WHERE user_id = ? AND distributor_id = ? AND season = ? AND category = ?
           ORDER BY id DESC""",
        (user_id, distributor_id, season, category),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        rows_data = json.loads(d.pop("rows_json", "[]"))
        # Extract SO numbers and article summary
        so_numbers = set()
        articles = []
        for row in rows_data:
            for sn in (row.get("so_numbers") or []):
                so_numbers.add(sn)
            art_code = row.get("article_code") or row.get("fo_article_code") or ""
            art_name = row.get("article_name") or row.get("fo_article_name") or ""
            articles.append({
                "article_code": art_code,
                "article_name": art_name,
                "so_qty": float(row.get("so_qty") or 0),
                "fo_qty": float(row.get("fo_qty") or 0),
                "so_net_amount": float(row.get("so_net_amount") or 0),
            })
        d["so_numbers"] = sorted(so_numbers)
        d["articles"] = articles
        result.append(d)
    return result

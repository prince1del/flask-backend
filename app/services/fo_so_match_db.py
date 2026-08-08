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
"""

_schema_ensured = False


def ensure_schema(conn: sqlite3.Connection) -> None:
    global _schema_ensured
    if _schema_ensured:
        try:
            conn.execute("SELECT 1 FROM fo_so_match_runs LIMIT 1")
            return
        except sqlite3.OperationalError:
            _schema_ensured = False
    conn.executescript(SCHEMA_SQL)
    conn.commit()
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
    "rows_json", "created_at",
]


def save_match_run(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    match_payload: dict[str, Any],
    so_buyer_label: str | None = None,
    so_source_filename: str | None = None,
) -> dict[str, Any]:
    """Insert a match run from run_match_saved_fo_vs_so_pack result."""
    ensure_schema(conn)
    fo = match_payload.get("fo") or {}
    match = match_payload.get("match") or {}
    totals = match.get("totals") or {}
    counts = match.get("counts") or {}
    rows = match.get("rows") or []

    mismatch = int(counts.get("QTY_MISMATCH") or 0) + int(counts.get("VALUE_MISMATCH") or 0)
    conn.execute(
        """INSERT INTO fo_so_match_runs (
            user_id, filled_order_id, distributor_id, distributor_name,
            category, season, fo_source_filename, so_buyer_label, so_source_filename,
            fo_qty, so_qty, delta_qty, fo_exmill_value, so_net_amount, delta_value,
            match_count, fuzzy_count, mismatch_count, missing_count, extra_count,
            rows_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            _now(),
        ),
    )
    conn.commit()
    run_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
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
        rows = json.loads(data.pop("rows_json") or "[]")
    except json.JSONDecodeError:
        rows = []
        data.pop("rows_json", None)
    # Normalize so_numbers to strings so mobile can show "which SO" per line.
    if isinstance(rows, list):
        for r in rows:
            if not isinstance(r, dict):
                continue
            nums = r.get("so_numbers")
            if nums is not None:
                if not isinstance(nums, list):
                    nums = [nums]
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


def list_match_runs(
    conn: sqlite3.Connection,
    user_id: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """List match runs. user_id=None → all runs (shared with BD app / team)."""
    ensure_schema(conn)
    cols = ", ".join(c for c in RUN_COLUMNS if c != "rows_json")
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
    keys = [c for c in RUN_COLUMNS if c != "rows_json"]
    return [_row_to_dict(r, keys) for r in rows]


def delete_match_run(conn: sqlite3.Connection, user_id: int, run_id: int) -> bool:
    ensure_schema(conn)
    cur = conn.execute(
        "DELETE FROM fo_so_match_runs WHERE id = ? AND user_id = ?",
        (run_id, user_id),
    )
    conn.commit()
    return cur.rowcount > 0

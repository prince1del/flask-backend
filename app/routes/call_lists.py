"""
Call List / MBO outreach — user-scoped prospect sheets.

Standalone from Party Master / Market Visit / Approach. Optional
linked_distributor_id is stored NULL for now (future link).
"""

from __future__ import annotations

import io
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request, send_file
from openpyxl import Workbook, load_workbook
from werkzeug.utils import secure_filename

from app.routes.auth import get_request_user_id, get_workspace_id, require_jwt_auth, require_role

call_lists_bp = Blueprint("call_lists", __name__, url_prefix="/api/v1/call-lists")

CALL_STATUSES = {
    "pending",
    "called",
    "follow_up",
    "not_interested",
    "wrong_number",
    "deal_done",
    "no_answer",
}


def _db_path() -> str:
    return current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_user() -> tuple[int | None, str | None, tuple | None]:
    uid = get_request_user_id()
    if uid is None:
        return None, None, (
            jsonify(
                {
                    "success": False,
                    "error": {"code": "NO_USER", "message": "User id required"},
                }
            ),
            401,
        )
    return uid, get_workspace_id(), None


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS call_list_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            source_filename TEXT,
            category_hint TEXT,
            row_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS call_list_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            workspace_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            serial_no INTEGER,
            shop_name TEXT,
            address TEXT,
            town_city TEXT,
            district TEXT,
            state TEXT,
            pin_code TEXT,
            phone TEXT,
            email TEXT,
            call_status TEXT NOT NULL DEFAULT 'pending',
            profile_notes TEXT,
            brands_carried TEXT,
            deals_in TEXT,
            feedback_note TEXT,
            last_called_at TEXT,
            -- Future: optional link to Party Master (never required)
            linked_distributor_id INTEGER,
            linked_party_kind TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(batch_id) REFERENCES call_list_batches(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_call_list_batches_user "
        "ON call_list_batches(user_id, workspace_id, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_call_list_rows_batch "
        "ON call_list_rows(batch_id, user_id, call_status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_call_list_rows_geo "
        "ON call_list_rows(batch_id, state, town_city, district)"
    )


def _norm_header(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _map_headers(row: tuple[Any, ...]) -> dict[str, int]:
    aliases = {
        "serial_no": {"s no", "sno", "s n", "serial", "sr no", "sr", "no"},
        "shop_name": {
            "shop name",
            "firm name",
            "store name",
            "name",
            "party name",
            "outlet",
        },
        "address": {"address", "addr"},
        "town_city": {"town city", "town", "city", "city town"},
        "district": {"district", "dist"},
        "state": {"state"},
        "pin_code": {"pin code", "pincode", "pin", "zip"},
        "phone": {
            "phone number",
            "phone",
            "mobile",
            "contact",
            "contact no",
            "contact number",
            "mobile number",
        },
        "email": {"email", "e mail", "mail"},
    }
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(row):
        key = _norm_header(cell)
        if not key:
            continue
        for field, names in aliases.items():
            if key in names and field not in mapping:
                mapping[field] = idx
                break
    return mapping


def _cell(row: tuple[Any, ...], idx: int | None) -> str | None:
    if idx is None or idx >= len(row):
        return None
    val = row[idx]
    if val is None:
        return None
    text = str(val).strip()
    return text or None


def _parse_workbook(file_bytes: bytes) -> tuple[str | None, list[dict[str, Any]]]:
    wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()

    category_hint = None
    if rows:
        first = rows[0]
        if first and first[0] and not _norm_header(first[0]).startswith("s no"):
            hint = str(first[0]).strip()
            if len(hint) > 3:
                category_hint = hint[:240]

    header_idx = None
    mapping: dict[str, int] = {}
    for i, row in enumerate(rows[:40]):
        if not row:
            continue
        m = _map_headers(tuple(row))
        if "shop_name" in m or "phone" in m:
            header_idx = i
            mapping = m
            break
    if header_idx is None:
        raise ValueError(
            "Could not find header row (need Shop Name / Phone columns)"
        )

    parsed: list[dict[str, Any]] = []
    for row in rows[header_idx + 1 :]:
        if not row or not any(c is not None and str(c).strip() for c in row):
            continue
        shop = _cell(row, mapping.get("shop_name"))
        phone = _cell(row, mapping.get("phone"))
        if not shop and not phone:
            continue
        serial_raw = _cell(row, mapping.get("serial_no"))
        serial_no = None
        if serial_raw:
            try:
                serial_no = int(float(serial_raw))
            except (TypeError, ValueError):
                serial_no = None
        parsed.append(
            {
                "serial_no": serial_no,
                "shop_name": shop,
                "address": _cell(row, mapping.get("address")),
                "town_city": _cell(row, mapping.get("town_city")),
                "district": _cell(row, mapping.get("district")),
                "state": _cell(row, mapping.get("state")),
                "pin_code": _cell(row, mapping.get("pin_code")),
                "phone": phone,
                "email": _cell(row, mapping.get("email")),
            }
        )
    if not parsed:
        raise ValueError("No shop rows found in the spreadsheet")
    return category_hint, parsed


def _batch_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "source_filename": row["source_filename"],
        "category_hint": row["category_hint"],
        "row_count": row["row_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "batch_id": row["batch_id"],
        "serial_no": row["serial_no"],
        "shop_name": row["shop_name"],
        "address": row["address"],
        "town_city": row["town_city"],
        "district": row["district"],
        "state": row["state"],
        "pin_code": row["pin_code"],
        "phone": row["phone"],
        "email": row["email"],
        "call_status": row["call_status"] or "pending",
        "profile_notes": row["profile_notes"],
        "brands_carried": row["brands_carried"],
        "deals_in": row["deals_in"],
        "feedback_note": row["feedback_note"],
        "last_called_at": row["last_called_at"],
        "linked_distributor_id": row["linked_distributor_id"],
        "linked_party_kind": row["linked_party_kind"],
        "updated_at": row["updated_at"],
    }


def _get_owned_batch(
    conn: sqlite3.Connection, batch_id: int, user_id: int, workspace_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM call_list_batches
        WHERE id = ? AND user_id = ? AND workspace_id = ?
        """,
        (batch_id, user_id, workspace_id),
    ).fetchone()


@call_lists_bp.route("", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def list_batches():
    uid, workspace_id, err = _require_user()
    if err:
        return err
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_tables(conn)
        rows = conn.execute(
            """
            SELECT * FROM call_list_batches
            WHERE user_id = ? AND workspace_id = ?
            ORDER BY id DESC
            """,
            (uid, workspace_id),
        ).fetchall()
    return jsonify({"success": True, "data": [_batch_dict(r) for r in rows]}), 200


@call_lists_bp.route("/upload", methods=["POST"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def upload_batch():
    uid, workspace_id, err = _require_user()
    if err:
        return err
    file = request.files.get("file")
    if file is None or not file.filename:
        return (
            jsonify(
                {
                    "success": False,
                    "error": {"message": "Excel file required (field: file)"},
                }
            ),
            400,
        )
    filename = secure_filename(file.filename) or "call_list.xlsx"
    raw = file.read()
    if not raw:
        return jsonify({"success": False, "error": {"message": "Empty file"}}), 400

    title = (request.form.get("title") or "").strip()
    if not title:
        title = filename.rsplit(".", 1)[0].replace("_", " ").strip() or "Call List"

    try:
        category_hint, parsed = _parse_workbook(raw)
    except Exception as exc:
        return (
            jsonify({"success": False, "error": {"message": str(exc)}}),
            400,
        )

    now = _now_iso()
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_tables(conn)
        cur = conn.execute(
            """
            INSERT INTO call_list_batches (
                workspace_id, user_id, title, source_filename, category_hint,
                row_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                uid,
                title,
                filename,
                category_hint,
                len(parsed),
                now,
                now,
            ),
        )
        batch_id = int(cur.lastrowid)
        conn.executemany(
            """
            INSERT INTO call_list_rows (
                batch_id, workspace_id, user_id, serial_no, shop_name, address,
                town_city, district, state, pin_code, phone, email,
                call_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            [
                (
                    batch_id,
                    workspace_id,
                    uid,
                    r["serial_no"],
                    r["shop_name"],
                    r["address"],
                    r["town_city"],
                    r["district"],
                    r["state"],
                    r["pin_code"],
                    r["phone"],
                    r["email"],
                    now,
                    now,
                )
                for r in parsed
            ],
        )
        conn.commit()
        batch = _get_owned_batch(conn, batch_id, uid, workspace_id)

    return jsonify({"success": True, "data": _batch_dict(batch)}), 201


@call_lists_bp.route("/<int:batch_id>", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def get_batch(batch_id: int):
    uid, workspace_id, err = _require_user()
    if err:
        return err
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_tables(conn)
        batch = _get_owned_batch(conn, batch_id, uid, workspace_id)
        if not batch:
            return (
                jsonify({"success": False, "error": {"message": "List not found"}}),
                404,
            )
        states = [
            r[0]
            for r in conn.execute(
                """
                SELECT DISTINCT state FROM call_list_rows
                WHERE batch_id = ? AND user_id = ? AND IFNULL(TRIM(state),'') != ''
                ORDER BY state COLLATE NOCASE
                """,
                (batch_id, uid),
            ).fetchall()
        ]
        cities = [
            r[0]
            for r in conn.execute(
                """
                SELECT DISTINCT town_city FROM call_list_rows
                WHERE batch_id = ? AND user_id = ?
                  AND IFNULL(TRIM(town_city),'') != ''
                ORDER BY town_city COLLATE NOCASE
                """,
                (batch_id, uid),
            ).fetchall()
        ]
        # Linked State → Cities map for cascading dropdowns on mobile.
        cities_by_state: dict[str, list[str]] = {}
        for st, city in conn.execute(
            """
            SELECT DISTINCT state, town_city FROM call_list_rows
            WHERE batch_id = ? AND user_id = ?
              AND IFNULL(TRIM(state),'') != ''
              AND IFNULL(TRIM(town_city),'') != ''
            ORDER BY state COLLATE NOCASE, town_city COLLATE NOCASE
            """,
            (batch_id, uid),
        ).fetchall():
            key = str(st).strip()
            val = str(city).strip()
            if not key or not val:
                continue
            bucket = cities_by_state.setdefault(key, [])
            if val not in bucket:
                bucket.append(val)
        districts = [
            r[0]
            for r in conn.execute(
                """
                SELECT DISTINCT district FROM call_list_rows
                WHERE batch_id = ? AND user_id = ?
                  AND IFNULL(TRIM(district),'') != ''
                ORDER BY district COLLATE NOCASE
                """,
                (batch_id, uid),
            ).fetchall()
        ]
        status_rows = conn.execute(
            """
            SELECT call_status, COUNT(*) AS c FROM call_list_rows
            WHERE batch_id = ? AND user_id = ?
            GROUP BY call_status
            """,
            (batch_id, uid),
        ).fetchall()
        status_counts = {r["call_status"] or "pending": int(r["c"]) for r in status_rows}

    data = _batch_dict(batch)
    data["filters"] = {
        "states": states,
        "cities": cities,
        "cities_by_state": cities_by_state,
        "districts": districts,
        "status_counts": status_counts,
    }
    return jsonify({"success": True, "data": data}), 200


@call_lists_bp.route("/<int:batch_id>/rows", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def list_rows(batch_id: int):
    uid, workspace_id, err = _require_user()
    if err:
        return err
    limit = min(max(request.args.get("limit", 100, type=int) or 100, 1), 500)
    offset = max(request.args.get("offset", 0, type=int) or 0, 0)
    state = (request.args.get("state") or "").strip()
    city = (request.args.get("city") or "").strip()
    district = (request.args.get("district") or "").strip()
    status = (request.args.get("status") or "").strip().lower()
    q = (request.args.get("q") or "").strip()

    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_tables(conn)
        batch = _get_owned_batch(conn, batch_id, uid, workspace_id)
        if not batch:
            return (
                jsonify({"success": False, "error": {"message": "List not found"}}),
                404,
            )
        where = ["batch_id = ?", "user_id = ?"]
        params: list[Any] = [batch_id, uid]
        if state:
            where.append("LOWER(IFNULL(state,'')) = LOWER(?)")
            params.append(state)
        if city:
            where.append("LOWER(IFNULL(town_city,'')) = LOWER(?)")
            params.append(city)
        if district:
            where.append("LOWER(IFNULL(district,'')) = LOWER(?)")
            params.append(district)
        if status:
            where.append("LOWER(IFNULL(call_status,'pending')) = ?")
            params.append(status)
        if q:
            where.append(
                "("
                "IFNULL(shop_name,'') LIKE ? OR IFNULL(phone,'') LIKE ? OR "
                "IFNULL(town_city,'') LIKE ? OR IFNULL(address,'') LIKE ?"
                ")"
            )
            like = f"%{q}%"
            params.extend([like, like, like, like])
        where_sql = " AND ".join(where)
        total = conn.execute(
            f"SELECT COUNT(*) FROM call_list_rows WHERE {where_sql}",
            tuple(params),
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT * FROM call_list_rows
            WHERE {where_sql}
            ORDER BY
                CASE WHEN IFNULL(call_status,'pending') = 'pending' THEN 0 ELSE 1 END,
                IFNULL(serial_no, 999999),
                id
            LIMIT ? OFFSET ?
            """,
            tuple(params + [limit, offset]),
        ).fetchall()

    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "total": int(total),
                    "limit": limit,
                    "offset": offset,
                    "rows": [_row_dict(r) for r in rows],
                },
            }
        ),
        200,
    )


@call_lists_bp.route("/rows/<int:row_id>", methods=["PATCH"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def update_row(row_id: int):
    uid, workspace_id, err = _require_user()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_tables(conn)
        row = conn.execute(
            """
            SELECT * FROM call_list_rows
            WHERE id = ? AND user_id = ? AND workspace_id = ?
            """,
            (row_id, uid, workspace_id),
        ).fetchone()
        if not row:
            return (
                jsonify({"success": False, "error": {"message": "Row not found"}}),
                404,
            )

        sets: list[str] = []
        params: list[Any] = []
        status = payload.get("call_status")
        if status is not None:
            st = str(status).strip().lower()
            if st not in CALL_STATUSES:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": {
                                "message": f"Invalid status. Use: {', '.join(sorted(CALL_STATUSES))}"
                            },
                        }
                    ),
                    400,
                )
            sets.append("call_status = ?")
            params.append(st)
            if st != "pending":
                sets.append("last_called_at = COALESCE(last_called_at, ?)")
                params.append(_now_iso())

        for field in (
            "profile_notes",
            "brands_carried",
            "deals_in",
            "feedback_note",
        ):
            if field in payload:
                val = payload.get(field)
                text = None if val is None else str(val).strip() or None
                sets.append(f"{field} = ?")
                params.append(text)

        # Future distributor link — accept but never auto-fill from Party Master.
        if "linked_distributor_id" in payload:
            raw = payload.get("linked_distributor_id")
            linked = None
            if raw is not None and str(raw).strip():
                try:
                    linked = int(raw)
                except (TypeError, ValueError):
                    return (
                        jsonify(
                            {
                                "success": False,
                                "error": {"message": "linked_distributor_id must be int"},
                            }
                        ),
                        400,
                    )
            sets.append("linked_distributor_id = ?")
            params.append(linked)
        if "linked_party_kind" in payload:
            kind = payload.get("linked_party_kind")
            sets.append("linked_party_kind = ?")
            params.append(
                None if kind is None else (str(kind).strip() or None)
            )

        if not sets:
            return jsonify({"success": True, "data": _row_dict(row)}), 200

        now = _now_iso()
        sets.append("updated_at = ?")
        params.append(now)
        params.append(row_id)
        conn.execute(
            f"UPDATE call_list_rows SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        conn.execute(
            "UPDATE call_list_batches SET updated_at = ? WHERE id = ?",
            (now, row["batch_id"]),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM call_list_rows WHERE id = ?", (row_id,)
        ).fetchone()

    return jsonify({"success": True, "data": _row_dict(updated)}), 200


@call_lists_bp.route("/<int:batch_id>/export", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def export_batch(batch_id: int):
    uid, workspace_id, err = _require_user()
    if err:
        return err
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_tables(conn)
        batch = _get_owned_batch(conn, batch_id, uid, workspace_id)
        if not batch:
            return (
                jsonify({"success": False, "error": {"message": "List not found"}}),
                404,
            )
        rows = conn.execute(
            """
            SELECT * FROM call_list_rows
            WHERE batch_id = ? AND user_id = ?
            ORDER BY IFNULL(serial_no, 999999), id
            """,
            (batch_id, uid),
        ).fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Call List Feedback"
    ws.append(
        [
            "S.No",
            "Shop Name",
            "Address",
            "Town/City",
            "District",
            "State",
            "Pin Code",
            "Phone Number",
            "Email",
            "Call Status",
            "Profile / What they do",
            "Brands carried",
            "Deals in",
            "Feedback note",
            "Last called at",
            "Linked distributor id (future)",
        ]
    )
    for r in rows:
        ws.append(
            [
                r["serial_no"],
                r["shop_name"],
                r["address"],
                r["town_city"],
                r["district"],
                r["state"],
                r["pin_code"],
                r["phone"],
                r["email"],
                r["call_status"],
                r["profile_notes"],
                r["brands_carried"],
                r["deals_in"],
                r["feedback_note"],
                r["last_called_at"],
                r["linked_distributor_id"],
            ]
        )

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    safe_title = re.sub(r"[^\w\-]+", "_", batch["title"] or "call_list")[:60]
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"{safe_title}_feedback.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@call_lists_bp.route("/<int:batch_id>", methods=["DELETE"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def delete_batch(batch_id: int):
    uid, workspace_id, err = _require_user()
    if err:
        return err
    with sqlite3.connect(_db_path()) as conn:
        _ensure_tables(conn)
        batch = conn.execute(
            """
            SELECT id FROM call_list_batches
            WHERE id = ? AND user_id = ? AND workspace_id = ?
            """,
            (batch_id, uid, workspace_id),
        ).fetchone()
        if not batch:
            return (
                jsonify({"success": False, "error": {"message": "List not found"}}),
                404,
            )
        conn.execute("DELETE FROM call_list_rows WHERE batch_id = ?", (batch_id,))
        conn.execute("DELETE FROM call_list_batches WHERE id = ?", (batch_id,))
        conn.commit()
    return jsonify({"success": True, "data": {"deleted": batch_id}}), 200

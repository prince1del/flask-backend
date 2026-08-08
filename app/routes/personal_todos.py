"""Personal To-Do / work diary — user-scoped, not CRM workflow."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, request

from app.routes.auth import get_request_user_id, get_workspace_id, require_jwt_auth, require_role

personal_todos_bp = Blueprint(
    "personal_todos", __name__, url_prefix="/api/v1/personal-todos"
)

CATEGORIES = {
    "Boss Task",
    "Retailer Query",
    "Distributor Query",
    "Call",
    "Report",
    "Internal Work",
    "Other",
}
PRIORITIES = {"normal", "important", "urgent"}
STATUSES = {"pending", "hold", "done"}


def _db_path() -> str:
    return current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_local() -> date:
    return datetime.now().date()


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS personal_todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            task_title TEXT NOT NULL,
            category TEXT,
            person_party TEXT,
            given_by TEXT,
            remarks TEXT,
            priority TEXT NOT NULL DEFAULT 'normal',
            status TEXT NOT NULL DEFAULT 'pending',
            due_date TEXT,
            due_time TEXT,
            reminder_datetime TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            linked_party_id INTEGER,
            linked_distributor_id INTEGER,
            linked_retailer_id INTEGER
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_personal_todos_user "
        "ON personal_todos(workspace_id, user_id, status, due_date)"
    )


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "user_id": row["user_id"],
        "username": row["username"],
        "task_title": row["task_title"],
        "category": row["category"],
        "person_party": row["person_party"],
        "given_by": row["given_by"],
        "remarks": row["remarks"],
        "priority": row["priority"],
        "status": row["status"],
        "due_date": row["due_date"],
        "due_time": row["due_time"],
        "reminder_datetime": row["reminder_datetime"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "linked_party_id": row["linked_party_id"],
        "linked_distributor_id": row["linked_distributor_id"],
        "linked_retailer_id": row["linked_retailer_id"],
        "bucket": _bucket_for_row(row),
    }


def _bucket_for_row(row: sqlite3.Row | dict) -> str:
    status = (row["status"] if not isinstance(row, dict) else row.get("status")) or "pending"
    if status == "done":
        return "completed"
    if status == "hold":
        return "hold"
    due_date = (row["due_date"] if not isinstance(row, dict) else row.get("due_date")) or ""
    due_date = str(due_date).strip()
    if not due_date:
        return "no_due"
    today = _today_local().isoformat()
    if due_date < today:
        return "overdue"
    if due_date == today:
        return "today"
    return "upcoming"


def _current_user() -> dict:
    return getattr(request, "user", None) or {}


def _require_user_id() -> tuple[int | None, tuple | None]:
    uid = get_request_user_id()
    if uid is None:
        return None, (
            jsonify(
                {
                    "success": False,
                    "error": {"message": "User id required", "code": "USER_REQUIRED"},
                }
            ),
            401,
        )
    return uid, None


def _get_owned(conn: sqlite3.Connection, todo_id: int, workspace_id: str, user_id: int):
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT * FROM personal_todos
        WHERE id = ? AND workspace_id = ? AND user_id = ?
        """,
        (todo_id, workspace_id, user_id),
    ).fetchone()


def _normalize_payload(data: dict, *, partial: bool = False) -> tuple[dict | None, str | None]:
    out: dict = {}
    if not partial or "task_title" in data:
        title = (data.get("task_title") or "").strip()
        if not title:
            return None, "task_title is required"
        out["task_title"] = title

    if not partial or "category" in data:
        cat = (data.get("category") or "").strip() or None
        if cat and cat not in CATEGORIES:
            return None, f"category must be one of: {', '.join(sorted(CATEGORIES))}"
        out["category"] = cat

    if not partial or "person_party" in data:
        out["person_party"] = (data.get("person_party") or "").strip() or None
    if not partial or "given_by" in data:
        out["given_by"] = (data.get("given_by") or "").strip() or None
    if not partial or "remarks" in data:
        out["remarks"] = (data.get("remarks") or "").strip() or None

    if not partial or "priority" in data:
        pri = (data.get("priority") or "normal").strip().lower()
        if pri not in PRIORITIES:
            return None, "priority must be normal, important, or urgent"
        out["priority"] = pri

    if not partial or "status" in data:
        st = (data.get("status") or "pending").strip().lower()
        if st not in STATUSES:
            return None, "status must be pending, hold, or done"
        out["status"] = st

    if not partial or "due_date" in data:
        due = (data.get("due_date") or "").strip() or None
        out["due_date"] = due
    if not partial or "due_time" in data:
        out["due_time"] = (data.get("due_time") or "").strip() or None
    if not partial or "reminder_datetime" in data:
        out["reminder_datetime"] = (data.get("reminder_datetime") or "").strip() or None

    return out, None


@personal_todos_bp.route("", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def list_todos():
    uid, err = _require_user_id()
    if err:
        return err
    workspace_id = get_workspace_id()
    status = (request.args.get("status") or "").strip().lower() or None
    category = (request.args.get("category") or "").strip() or None
    q = (request.args.get("q") or "").strip().lower() or None
    bucket = (request.args.get("bucket") or "all").strip().lower()

    with sqlite3.connect(_db_path()) as conn:
        _ensure_table(conn)
        conn.row_factory = sqlite3.Row
        sql = """
            SELECT * FROM personal_todos
            WHERE workspace_id = ? AND user_id = ?
        """
        params: list = [workspace_id, uid]
        if status:
            sql += " AND status = ?"
            params.append(status)
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'hold' THEN 1 ELSE 2 END, due_date IS NULL, due_date ASC, id DESC"
        rows = conn.execute(sql, tuple(params)).fetchall()

    items = [_row_to_dict(r) for r in rows]
    if q:
        items = [
            t
            for t in items
            if q in (t.get("task_title") or "").lower()
            or q in (t.get("person_party") or "").lower()
            or q in (t.get("given_by") or "").lower()
            or q in (t.get("remarks") or "").lower()
            or q in (t.get("category") or "").lower()
        ]
    if bucket and bucket != "all":
        items = [t for t in items if t.get("bucket") == bucket]

    return jsonify({"success": True, "data": {"todos": items, "count": len(items)}})


@personal_todos_bp.route("/summary", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def todos_summary():
    uid, err = _require_user_id()
    if err:
        return err
    workspace_id = get_workspace_id()
    with sqlite3.connect(_db_path()) as conn:
        _ensure_table(conn)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM personal_todos
            WHERE workspace_id = ? AND user_id = ? AND status != 'done'
            ORDER BY due_date IS NULL, due_date ASC, id DESC
            """,
            (workspace_id, uid),
        ).fetchall()
    items = [_row_to_dict(r) for r in rows]
    overdue = [t for t in items if t["bucket"] == "overdue"]
    today = [t for t in items if t["bucket"] == "today"]
    upcoming = [t for t in items if t["bucket"] == "upcoming"]
    hold = [t for t in items if t["bucket"] == "hold"]
    preview = [t for t in items if t["bucket"] != "hold"][:5]
    return jsonify(
        {
            "success": True,
            "data": {
                "overdue_count": len(overdue),
                "today_count": len(today),
                "upcoming_count": len(upcoming),
                "hold_count": len(hold),
                "preview": preview,
            },
        }
    )


@personal_todos_bp.route("", methods=["POST"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def create_todo():
    uid, err = _require_user_id()
    if err:
        return err
    workspace_id = get_workspace_id()
    user = _current_user()
    data = request.get_json(silent=True) or {}
    payload, msg = _normalize_payload(data, partial=False)
    if payload is None:
        return jsonify({"success": False, "error": {"message": msg}}), 400

    now = _now_iso()
    status = payload.get("status") or "pending"
    completed_at = now if status == "done" else None

    with sqlite3.connect(_db_path()) as conn:
        _ensure_table(conn)
        cur = conn.execute(
            """
            INSERT INTO personal_todos (
                workspace_id, user_id, username, task_title, category, person_party,
                given_by, remarks, priority, status, due_date, due_time,
                reminder_datetime, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                uid,
                user.get("username"),
                payload["task_title"],
                payload.get("category"),
                payload.get("person_party"),
                payload.get("given_by"),
                payload.get("remarks"),
                payload.get("priority") or "normal",
                status,
                payload.get("due_date"),
                payload.get("due_time"),
                payload.get("reminder_datetime"),
                now,
                now,
                completed_at,
            ),
        )
        todo_id = int(cur.lastrowid)
        conn.commit()
        row = _get_owned(conn, todo_id, workspace_id, uid)

    return jsonify({"success": True, "data": _row_to_dict(row)}), 201


@personal_todos_bp.route("/<int:todo_id>", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def get_todo(todo_id: int):
    uid, err = _require_user_id()
    if err:
        return err
    workspace_id = get_workspace_id()
    with sqlite3.connect(_db_path()) as conn:
        _ensure_table(conn)
        row = _get_owned(conn, todo_id, workspace_id, uid)
    if not row:
        return jsonify({"success": False, "error": {"message": "To-Do not found"}}), 404
    return jsonify({"success": True, "data": _row_to_dict(row)})


@personal_todos_bp.route("/<int:todo_id>", methods=["PATCH"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def update_todo(todo_id: int):
    uid, err = _require_user_id()
    if err:
        return err
    workspace_id = get_workspace_id()
    data = request.get_json(silent=True) or {}
    payload, msg = _normalize_payload(data, partial=True)
    if payload is None:
        return jsonify({"success": False, "error": {"message": msg}}), 400
    if not payload:
        return jsonify({"success": False, "error": {"message": "No fields to update"}}), 400

    with sqlite3.connect(_db_path()) as conn:
        _ensure_table(conn)
        row = _get_owned(conn, todo_id, workspace_id, uid)
        if not row:
            return jsonify({"success": False, "error": {"message": "To-Do not found"}}), 404

        now = _now_iso()
        fields = []
        values: list = []
        for key in (
            "task_title",
            "category",
            "person_party",
            "given_by",
            "remarks",
            "priority",
            "status",
            "due_date",
            "due_time",
            "reminder_datetime",
        ):
            if key in payload:
                fields.append(f"{key} = ?")
                values.append(payload[key])

        new_status = payload.get("status", row["status"])
        if new_status == "done" and row["status"] != "done":
            fields.append("completed_at = ?")
            values.append(now)
        elif new_status != "done" and row["status"] == "done":
            fields.append("completed_at = ?")
            values.append(None)

        fields.append("updated_at = ?")
        values.append(now)
        values.extend([todo_id, workspace_id, uid])
        conn.execute(
            f"UPDATE personal_todos SET {', '.join(fields)} "
            "WHERE id = ? AND workspace_id = ? AND user_id = ?",
            tuple(values),
        )
        conn.commit()
        row = _get_owned(conn, todo_id, workspace_id, uid)

    return jsonify({"success": True, "data": _row_to_dict(row)})


@personal_todos_bp.route("/<int:todo_id>", methods=["DELETE"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def delete_todo(todo_id: int):
    uid, err = _require_user_id()
    if err:
        return err
    workspace_id = get_workspace_id()
    with sqlite3.connect(_db_path()) as conn:
        _ensure_table(conn)
        row = _get_owned(conn, todo_id, workspace_id, uid)
        if not row:
            return jsonify({"success": False, "error": {"message": "To-Do not found"}}), 404
        conn.execute(
            "DELETE FROM personal_todos WHERE id = ? AND workspace_id = ? AND user_id = ?",
            (todo_id, workspace_id, uid),
        )
        conn.commit()
    return jsonify({"success": True, "data": {"deleted": True, "id": todo_id}})


def _set_status(todo_id: int, status: str):
    uid, err = _require_user_id()
    if err:
        return err
    workspace_id = get_workspace_id()
    now = _now_iso()
    with sqlite3.connect(_db_path()) as conn:
        _ensure_table(conn)
        row = _get_owned(conn, todo_id, workspace_id, uid)
        if not row:
            return jsonify({"success": False, "error": {"message": "To-Do not found"}}), 404
        completed_at = now if status == "done" else None
        conn.execute(
            """
            UPDATE personal_todos
            SET status = ?, completed_at = ?, updated_at = ?
            WHERE id = ? AND workspace_id = ? AND user_id = ?
            """,
            (status, completed_at, now, todo_id, workspace_id, uid),
        )
        conn.commit()
        row = _get_owned(conn, todo_id, workspace_id, uid)
    return jsonify({"success": True, "data": _row_to_dict(row)})


@personal_todos_bp.route("/<int:todo_id>/done", methods=["POST"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def mark_done(todo_id: int):
    return _set_status(todo_id, "done")


@personal_todos_bp.route("/<int:todo_id>/hold", methods=["POST"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def mark_hold(todo_id: int):
    return _set_status(todo_id, "hold")


@personal_todos_bp.route("/<int:todo_id>/reopen", methods=["POST"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def reopen_todo(todo_id: int):
    return _set_status(todo_id, "pending")


@personal_todos_bp.route("/presets/due-dates", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def due_presets():
    today = _today_local()
    return jsonify(
        {
            "success": True,
            "data": {
                "today": today.isoformat(),
                "tomorrow": (today + timedelta(days=1)).isoformat(),
            },
        }
    )

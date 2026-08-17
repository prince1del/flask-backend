"""Distributor grievances — complaints/problems per distributor, synced to Personal To-Do."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from app.routes.auth import get_request_user_id, get_workspace_id, require_jwt_auth, require_role
from centralized_db_system.db import CentralizedDB

distributor_grievances_bp = Blueprint(
    "distributor_grievances",
    __name__,
    url_prefix="/api/v1/distributor-grievances",
)

STATUSES = {"open", "closed"}


def _db_path() -> str:
    return current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _ensure_grievances_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS distributor_grievances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            distributor_id INTEGER NOT NULL,
            distributor_name TEXT NOT NULL,
            problem_text TEXT NOT NULL,
            problem_date TEXT NOT NULL,
            email_sent_at TEXT,
            email_subject TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            solution_text TEXT,
            linked_todo_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            closed_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dg_user_status "
        "ON distributor_grievances(workspace_id, user_id, status, problem_date)"
    )
    try:
        conn.execute("ALTER TABLE distributor_grievances ADD COLUMN complaint_mode TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE distributor_grievances ADD COLUMN follow_ups_json TEXT")
    except sqlite3.OperationalError:
        pass


def _ensure_personal_todos_columns(conn: sqlite3.Connection) -> None:
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
    for col, ddl in (
        ("linked_grievance_id", "INTEGER"),
    ):
        try:
            conn.execute(f"ALTER TABLE personal_todos ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass


def _parse_follow_ups(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _row_to_dict(row: sqlite3.Row) -> dict:
    status = (row["status"] or "open").strip().lower()
    keys = row.keys()
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "user_id": row["user_id"],
        "distributor_id": row["distributor_id"],
        "distributor_name": row["distributor_name"],
        "problem_text": row["problem_text"],
        "problem_date": row["problem_date"],
        "complaint_mode": row["complaint_mode"] if "complaint_mode" in keys else None,
        "email_sent_at": row["email_sent_at"],
        "email_subject": row["email_subject"],
        "status": status,
        "is_open": status != "closed",
        "solution_text": row["solution_text"],
        "follow_ups": _parse_follow_ups(row["follow_ups_json"] if "follow_ups_json" in keys else None),
        "linked_todo_id": row["linked_todo_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "closed_at": row["closed_at"],
    }


def _get_owned(conn: sqlite3.Connection, grievance_id: int, workspace_id: str, user_id: int):
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT * FROM distributor_grievances
        WHERE id = ? AND workspace_id = ? AND user_id = ?
        """,
        (grievance_id, workspace_id, user_id),
    ).fetchone()


def _truncate_title(text: str, max_len: int = 80) -> str:
    t = (text or "").strip().replace("\n", " ")
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


def _create_linked_todo(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    user_id: int,
    username: str | None,
    grievance_id: int,
    distributor_id: int,
    distributor_name: str,
    problem_text: str,
    problem_date: str,
) -> int:
    _ensure_personal_todos_columns(conn)
    now = _now_iso()
    title = _truncate_title(problem_text) or f"Grievance: {distributor_name}"
    cur = conn.execute(
        """
        INSERT INTO personal_todos (
            workspace_id, user_id, username, task_title, category, person_party,
            given_by, remarks, priority, status, due_date, due_time,
            reminder_datetime, created_at, updated_at, completed_at,
            linked_distributor_id, linked_grievance_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            user_id,
            username,
            title,
            "Grievance",
            distributor_name,
            None,
            problem_text.strip(),
            "important",
            "pending",
            problem_date,
            None,
            None,
            now,
            now,
            None,
            distributor_id,
            grievance_id,
        ),
    )
    return int(cur.lastrowid)


@distributor_grievances_bp.route("/distributors", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def list_grievance_distributors():
    """All distributors for this user — active and inactive."""
    uid, err = _require_user_id()
    if err:
        return err
    workspace_id = get_workspace_id()
    db = CentralizedDB(_db_path())
    rows = db.list_master_distributors(
        limit=5000,
        workspace_id=workspace_id,
        offset=0,
        include_inactive=True,
        user_id=uid,
    )
    distributors = []
    for d in rows:
        pk = d.get("id")
        try:
            pk_int = int(pk) if pk is not None else None
        except (TypeError, ValueError):
            pk_int = None
        if pk_int is None:
            continue
        name = (d.get("firm_name") or d.get("name") or "").strip() or f"Distributor #{pk_int}"
        status = (d.get("status") or "active").strip().lower() or "active"
        distributors.append(
            {
                "id": pk_int,
                "name": name,
                "nick": (d.get("firm_nick_name") or "").strip() or None,
                "city": (d.get("location") or "").strip() or None,
                "status": status,
                "is_active": status != "inactive",
            }
        )
    distributors.sort(key=lambda x: (0 if x["is_active"] else 1, x["name"].lower()))
    return jsonify({"success": True, "data": {"distributors": distributors, "count": len(distributors)}})


@distributor_grievances_bp.route("", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def list_grievances():
    uid, err = _require_user_id()
    if err:
        return err
    workspace_id = get_workspace_id()
    status_filter = (request.args.get("status") or "all").strip().lower()
    distributor_id = request.args.get("distributor_id", type=int)

    with sqlite3.connect(_db_path()) as conn:
        _ensure_grievances_table(conn)
        conn.row_factory = sqlite3.Row
        query = """
            SELECT * FROM distributor_grievances
            WHERE workspace_id = ? AND user_id = ?
        """
        params: list = [workspace_id, uid]
        if status_filter in STATUSES:
            query += " AND status = ?"
            params.append(status_filter)
        if distributor_id:
            query += " AND distributor_id = ?"
            params.append(distributor_id)
        query += " ORDER BY problem_date DESC, id DESC"
        rows = conn.execute(query, tuple(params)).fetchall()

    items = [_row_to_dict(r) for r in rows]
    open_items = [g for g in items if g["status"] == "open"]
    closed_items = [g for g in items if g["status"] == "closed"]
    return jsonify(
        {
            "success": True,
            "data": {
                "grievances": items,
                "open": open_items,
                "closed": closed_items,
                "open_count": len(open_items),
                "closed_count": len(closed_items),
                "count": len(items),
            },
        }
    )


@distributor_grievances_bp.route("", methods=["POST"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def create_grievance():
    uid, err = _require_user_id()
    if err:
        return err
    workspace_id = get_workspace_id()
    user = _current_user()
    data = request.get_json(silent=True) or {}

    try:
        distributor_id = int(data.get("distributor_id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": {"message": "distributor_id is required"}}), 400

    problem_text = (data.get("problem_text") or data.get("problem") or "").strip()
    if not problem_text:
        return jsonify({"success": False, "error": {"message": "problem_text is required"}}), 400

    problem_date = (data.get("problem_date") or "").strip()
    if not problem_date:
        problem_date = datetime.now().date().isoformat()

    complaint_mode = (
        data.get("complaint_mode")
        or data.get("mode_of_complaint")
        or data.get("communication_mode")
        or ""
    ).strip() or None
    email_sent_at = (data.get("email_sent_at") or "").strip() or None
    email_subject = (data.get("email_subject") or "").strip() or None
    if complaint_mode and complaint_mode.lower() == "mail" and not email_subject:
        email_subject = None

    db = CentralizedDB(_db_path())
    dist = db.get_master_distributor(distributor_id, workspace_id=workspace_id, user_id=uid)
    if not dist:
        return jsonify({"success": False, "error": {"message": "Distributor not found"}}), 404

    distributor_name = (
        (dist.get("firm_name") or dist.get("name") or "").strip()
        or f"Distributor #{distributor_id}"
    )

    now = _now_iso()
    with sqlite3.connect(_db_path()) as conn:
        _ensure_grievances_table(conn)
        cur = conn.execute(
            """
            INSERT INTO distributor_grievances (
                workspace_id, user_id, distributor_id, distributor_name,
                problem_text, problem_date, complaint_mode, email_sent_at, email_subject,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                workspace_id,
                uid,
                distributor_id,
                distributor_name,
                problem_text,
                problem_date,
                complaint_mode,
                email_sent_at,
                email_subject,
                now,
                now,
            ),
        )
        grievance_id = int(cur.lastrowid)
        todo_id = _create_linked_todo(
            conn,
            workspace_id=workspace_id,
            user_id=uid,
            username=user.get("username"),
            grievance_id=grievance_id,
            distributor_id=distributor_id,
            distributor_name=distributor_name,
            problem_text=problem_text,
            problem_date=problem_date,
        )
        conn.execute(
            """
            UPDATE distributor_grievances
            SET linked_todo_id = ?, updated_at = ?
            WHERE id = ? AND workspace_id = ? AND user_id = ?
            """,
            (todo_id, now, grievance_id, workspace_id, uid),
        )
        conn.commit()
        row = _get_owned(conn, grievance_id, workspace_id, uid)

    return jsonify({"success": True, "data": _row_to_dict(row)}), 201


@distributor_grievances_bp.route("/<int:grievance_id>/resolve", methods=["POST"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def resolve_grievance(grievance_id: int):
    uid, err = _require_user_id()
    if err:
        return err
    workspace_id = get_workspace_id()
    data = request.get_json(silent=True) or {}
    solution = (data.get("solution_text") or data.get("solution") or "").strip()
    if not solution:
        return jsonify({"success": False, "error": {"message": "solution_text is required"}}), 400

    now = _now_iso()
    with sqlite3.connect(_db_path()) as conn:
        _ensure_grievances_table(conn)
        _ensure_personal_todos_columns(conn)
        row = _get_owned(conn, grievance_id, workspace_id, uid)
        if not row:
            return jsonify({"success": False, "error": {"message": "Grievance not found"}}), 404
        if (row["status"] or "").strip().lower() == "closed":
            return jsonify({"success": False, "error": {"message": "Already closed"}}), 400

        conn.execute(
            """
            UPDATE distributor_grievances
            SET status = 'closed', solution_text = ?, closed_at = ?, updated_at = ?
            WHERE id = ? AND workspace_id = ? AND user_id = ?
            """,
            (solution, now, now, grievance_id, workspace_id, uid),
        )
        todo_id = row["linked_todo_id"]
        if todo_id:
            conn.execute(
                """
                UPDATE personal_todos
                SET status = 'done', completed_at = ?, updated_at = ?
                WHERE id = ? AND workspace_id = ? AND user_id = ?
                """,
                (now, now, todo_id, workspace_id, uid),
            )
        conn.commit()
        updated = _get_owned(conn, grievance_id, workspace_id, uid)

    return jsonify({"success": True, "data": _row_to_dict(updated)})


@distributor_grievances_bp.route("/<int:grievance_id>/follow-up", methods=["POST"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def add_grievance_follow_up(grievance_id: int):
    """Log a follow-up. Open cases stay open; closed cases reopen and return to My To-Do."""
    uid, err = _require_user_id()
    if err:
        return err
    workspace_id = get_workspace_id()
    data = request.get_json(silent=True) or {}
    note = (
        data.get("follow_up_text")
        or data.get("note")
        or data.get("solution_text")
        or ""
    ).strip()
    if not note:
        return jsonify({"success": False, "error": {"message": "follow_up_text is required"}}), 400

    now = _now_iso()
    with sqlite3.connect(_db_path()) as conn:
        _ensure_grievances_table(conn)
        _ensure_personal_todos_columns(conn)
        row = _get_owned(conn, grievance_id, workspace_id, uid)
        if not row:
            return jsonify({"success": False, "error": {"message": "Grievance not found"}}), 404

        was_closed = (row["status"] or "").strip().lower() == "closed"
        keys = row.keys()
        follow_ups = _parse_follow_ups(row["follow_ups_json"] if "follow_ups_json" in keys else None)
        follow_ups.append({"text": note, "created_at": now})
        follow_ups_json = json.dumps(follow_ups, ensure_ascii=False)

        if was_closed:
            conn.execute(
                """
                UPDATE distributor_grievances
                SET follow_ups_json = ?, status = 'open', closed_at = NULL, updated_at = ?
                WHERE id = ? AND workspace_id = ? AND user_id = ?
                """,
                (follow_ups_json, now, grievance_id, workspace_id, uid),
            )
            todo_id = row["linked_todo_id"]
            reopened_todo = False
            if todo_id:
                cur = conn.execute(
                    """
                    UPDATE personal_todos
                    SET status = 'pending', completed_at = NULL, updated_at = ?
                    WHERE id = ? AND workspace_id = ? AND user_id = ?
                    """,
                    (now, todo_id, workspace_id, uid),
                )
                reopened_todo = cur.rowcount > 0
            if not reopened_todo:
                cur = conn.execute(
                    """
                    UPDATE personal_todos
                    SET status = 'pending', completed_at = NULL, updated_at = ?
                    WHERE linked_grievance_id = ? AND workspace_id = ? AND user_id = ?
                    """,
                    (now, grievance_id, workspace_id, uid),
                )
                reopened_todo = cur.rowcount > 0
            if not reopened_todo:
                new_todo_id = _create_linked_todo(
                    conn,
                    workspace_id=workspace_id,
                    user_id=uid,
                    username=None,
                    grievance_id=grievance_id,
                    distributor_id=int(row["distributor_id"] or 0),
                    distributor_name=(row["distributor_name"] or "").strip()
                    or f"Distributor #{row['distributor_id']}",
                    problem_text=row["problem_text"] or note,
                    problem_date=row["problem_date"] or now[:10],
                )
                conn.execute(
                    """
                    UPDATE distributor_grievances
                    SET linked_todo_id = ?, updated_at = ?
                    WHERE id = ? AND workspace_id = ? AND user_id = ?
                    """,
                    (new_todo_id, now, grievance_id, workspace_id, uid),
                )
        else:
            conn.execute(
                """
                UPDATE distributor_grievances
                SET follow_ups_json = ?, updated_at = ?
                WHERE id = ? AND workspace_id = ? AND user_id = ?
                """,
                (follow_ups_json, now, grievance_id, workspace_id, uid),
            )
        conn.commit()
        updated = _get_owned(conn, grievance_id, workspace_id, uid)

    return jsonify({
        "success": True,
        "reopened": was_closed,
        "data": _row_to_dict(updated),
    })


@distributor_grievances_bp.route("/<int:grievance_id>", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def get_grievance(grievance_id: int):
    uid, err = _require_user_id()
    if err:
        return err
    workspace_id = get_workspace_id()
    with sqlite3.connect(_db_path()) as conn:
        _ensure_grievances_table(conn)
        row = _get_owned(conn, grievance_id, workspace_id, uid)
    if not row:
        return jsonify({"success": False, "error": {"message": "Grievance not found"}}), 404
    return jsonify({"success": True, "data": _row_to_dict(row)})

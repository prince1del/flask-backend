"""Ask Nexora actions: the AI proposes, the user confirms, the app's own route runs.

The AI never changes data itself. `propose()` only saves a pending action with a
plain-language summary; nothing happens until the same user confirms it (button
or "haan"). `confirm()` then calls the app's normal route as that user, so the
route's own checks, history and undo apply - exactly as if the user had pressed
the button in the app. Delete actions are deliberately not on the list.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

PENDING_MINUTES = 15

# action -> how to run it. {x} in a path is filled from the params and removed from the body.
ACTIONS: dict[str, dict[str, Any]] = {
    "add_todo": {
        "label": "Add a To-Do",
        "method": "POST", "path": "/api/v1/personal-todos",
        "required": ["task_title"],
        "fields": "task_title, due_date (YYYY-MM-DD), due_time (HH:MM), priority (low/medium/high), "
                  "category, person_party, remarks",
    },
    "todo_done": {
        "label": "Mark a To-Do done",
        "method": "POST", "path": "/api/v1/personal-todos/{todo_id}/done",
        "required": ["todo_id"], "fields": "todo_id (from /api/v1/personal-todos)",
    },
    "add_grievance": {
        "label": "Log a distributor grievance",
        "method": "POST", "path": "/api/v1/distributor-grievances",
        "required": ["distributor_id", "problem_text"],
        "fields": "distributor_id, problem_text, problem_date (YYYY-MM-DD), mode_of_complaint",
    },
    "resolve_grievance": {
        "label": "Resolve a grievance",
        "method": "POST", "path": "/api/v1/distributor-grievances/{grievance_id}/resolve",
        "required": ["grievance_id", "solution_text"], "fields": "grievance_id, solution_text",
    },
    "set_pjp_day": {
        "label": "Set the journey plan for a day",
        "method": "PUT", "path": "/api/v1/pjp/days/{plan_date}",
        "required": ["plan_date"],
        "fields": "plan_date (YYYY-MM-DD), place_to_visit, day_type (work/leave/holiday), travel_kms",
    },
    "add_deposit": {
        "label": "Record a payment received (deposit)",
        "method": "POST", "path": "/api/v1/distributor-payments/deposits",
        "required": ["distributor_id", "season", "category", "amount", "payment_date"],
        "fields": "distributor_id, season (e.g. AW26), category (Bed/Bath/...), amount, payment_date (YYYY-MM-DD), note",
    },
    "add_so_payment": {
        "label": "Record a payment against one SO",
        "method": "POST", "path": "/api/v1/order-fulfillment/payment-collection/entries",
        "required": ["distributor_id", "tracking_id", "amount", "payment_date"],
        "fields": "distributor_id, tracking_id (the SO record), amount, payment_date (YYYY-MM-DD), note",
    },
    "set_cd_rate": {
        "label": "Set CD %",
        "method": "POST", "path": "/api/v1/distributor-payments/cd",
        "required": ["distributor_id", "season", "category", "cd_percent"],
        "fields": "distributor_id, season, category, cd_percent",
    },
    "set_tds": {
        "label": "Set TDS on/off",
        "method": "POST", "path": "/api/v1/distributor-payments/tds",
        "required": ["distributor_id", "season", "category", "tds_applicable"],
        "fields": "distributor_id, season, category, tds_applicable (true/false)",
    },
}

_PATH_PARAM_RX = {
    "plan_date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
}
_YES = {"haan", "han", "ha", "haa", "haanji", "ji", "yes", "y", "ok", "okay", "confirm", "kar do", "karo",
        "save", "save karo", "save kar do", "theek hai", "thik hai", "done", "bilkul", "haan karo", "haan kar do"}
_NO = {"nahi", "na", "nahin", "no", "cancel", "mat karo", "rehne do", "rahne do", "ruko", "stop"}


def catalog_text() -> str:
    return "\n".join(f"{k} - {v['label']}: {v['fields']}" for k, v in ACTIONS.items())


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ask_nexora_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            workspace_id TEXT NOT NULL,
            action TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            body_json TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            result_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            decided_at TEXT
        )"""
    )
    return conn


def build_request(action: str, params: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | str:
    """(method, path, body) for an action, or an error message."""
    spec = ACTIONS.get(action)
    if not spec:
        return f"Unknown action '{action}'. Allowed: {', '.join(ACTIONS)}"
    params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    missing = [f for f in spec["required"] if f not in params]
    if missing:
        return f"Missing for {action}: {', '.join(missing)}"
    body = dict(params)
    path = spec["path"]
    for key in re.findall(r"\{([a-z_]+)\}", path):
        value = str(body.pop(key, "")).strip()
        rx = _PATH_PARAM_RX.get(key, re.compile(r"^\d+$"))
        if not rx.match(value):
            return f"{key} must look like {'YYYY-MM-DD' if key == 'plan_date' else 'a number'}"
        path = path.replace("{" + key + "}", value)
    return spec["method"], path, body


def propose(db_path: str, *, user_id: int, workspace_id: str, action: str,
            params: dict[str, Any], summary: str) -> dict[str, Any]:
    built = build_request(action, params)
    if isinstance(built, str):
        return {"error": built}
    method, path, body = built
    summary = (summary or "").strip() or ACTIONS[action]["label"]
    conn = _conn(db_path)
    try:
        # One open proposal per user: a new one replaces the old.
        conn.execute("UPDATE ask_nexora_actions SET status='replaced', decided_at=datetime('now') "
                     "WHERE user_id=? AND workspace_id=? AND status='pending'", (user_id, workspace_id))
        cur = conn.execute(
            "INSERT INTO ask_nexora_actions (user_id, workspace_id, action, method, path, body_json, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, workspace_id, action, method, path, json.dumps(body, ensure_ascii=False), summary[:500]),
        )
        conn.commit()
        action_id = cur.lastrowid
    finally:
        conn.close()
    return {"action_id": action_id, "summary": summary, "status": "waiting_for_user_confirm"}


def pending(db_path: str, *, user_id: int, workspace_id: str) -> dict[str, Any] | None:
    """The user's open proposal from the last PENDING_MINUTES, if any."""
    conn = _conn(db_path)
    try:
        row = conn.execute(
            "SELECT id, action, summary, created_at FROM ask_nexora_actions WHERE user_id=? AND workspace_id=? "
            "AND status='pending' AND created_at >= datetime('now', ?) ORDER BY id DESC LIMIT 1",
            (user_id, workspace_id, f"-{PENDING_MINUTES} minutes"),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {"id": row[0], "action": row[1], "summary": row[2], "label": ACTIONS.get(row[1], {}).get("label", "")}


def _load(db_path: str, action_id: int, user_id: int, workspace_id: str):
    conn = _conn(db_path)
    try:
        return conn.execute(
            "SELECT id, action, method, path, body_json, summary, status, "
            "created_at >= datetime('now', ?) FROM ask_nexora_actions WHERE id=? AND user_id=? AND workspace_id=?",
            (f"-{PENDING_MINUTES} minutes", action_id, user_id, workspace_id),
        ).fetchone()
    finally:
        conn.close()


def _finish(db_path: str, action_id: int, status: str, result: Any) -> None:
    conn = _conn(db_path)
    try:
        conn.execute("UPDATE ask_nexora_actions SET status=?, result_json=?, decided_at=datetime('now') WHERE id=?",
                     (status, json.dumps(result, ensure_ascii=False, default=str)[:4000], action_id))
        conn.commit()
    finally:
        conn.close()


def confirm(db_path: str, *, action_id: int, user_id: int, workspace_id: str) -> dict[str, Any]:
    """Run a pending action through the app's own route, as this user."""
    from app.services.nexora_app_reader import call_as_user

    row = _load(db_path, action_id, user_id, workspace_id)
    if not row:
        return {"ok": False, "message": "This action was not found."}
    _id, action, method, path, body_json, summary, status, fresh = row
    if status != "pending":
        return {"ok": False, "message": f"Already {status}."}
    if not fresh:
        _finish(db_path, action_id, "expired", None)
        return {"ok": False, "message": "This proposal is older than 15 minutes. Please ask again."}
    # Claim it first so a double tap cannot run it twice.
    conn = _conn(db_path)
    try:
        claimed = conn.execute("UPDATE ask_nexora_actions SET status='running' WHERE id=? AND status='pending'",
                               (action_id,)).rowcount
        conn.commit()
    finally:
        conn.close()
    if not claimed:
        return {"ok": False, "message": "Already being saved."}
    resp = call_as_user(method, path, user_id=user_id, workspace_id=workspace_id, json_body=json.loads(body_json))
    data = resp.get_json(silent=True)
    ok = resp.status_code < 400 and not (isinstance(data, dict) and (data.get("success") is False or data.get("ok") is False))
    _finish(db_path, action_id, "done" if ok else "failed", {"status": resp.status_code, "body": data})
    if ok:
        return {"ok": True, "message": f"Done: {summary}", "result": data}
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        err = err.get("message")
    return {"ok": False, "message": f"Could not save: {err or f'error {resp.status_code}'}"}


def cancel(db_path: str, *, action_id: int, user_id: int, workspace_id: str) -> dict[str, Any]:
    row = _load(db_path, action_id, user_id, workspace_id)
    if not row or row[6] != "pending":
        return {"ok": False, "message": "Nothing to cancel."}
    _finish(db_path, action_id, "cancelled", None)
    return {"ok": True, "message": "Cancelled. Nothing was saved."}


def answer_reply(db_path: str, *, user_id: int, workspace_id: str, text: str) -> str | None:
    """If `text` is a plain yes/no and a proposal is open, confirm/cancel it and
    return the message to show; otherwise None (a normal question)."""
    kind = reply_kind(text)
    if kind is None:
        return None
    open_action = pending(db_path, user_id=user_id, workspace_id=workspace_id)
    if not open_action:
        return None
    if kind == "yes":
        out = confirm(db_path, action_id=open_action["id"], user_id=user_id, workspace_id=workspace_id)
    else:
        out = cancel(db_path, action_id=open_action["id"], user_id=user_id, workspace_id=workspace_id)
    return out["message"]


def reply_kind(text: str) -> str | None:
    """'yes' / 'no' when the whole message is a short confirmation, else None."""
    t = " ".join("".join(ch if ch.isalnum() else " " for ch in (text or "").lower()).split())
    if t in _YES:
        return "yes"
    if t in _NO:
        return "no"
    return None

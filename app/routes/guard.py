"""NEXORA Guard — /api/v1/guard/*: issues inbox, AI explain, daily AI digest.

Every login sees only its own inbox (House of Prizm: the company's books; Bombay
Dyeing logins: their own distributor payments). Nothing here changes business data.
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from app.routes.auth import get_workspace_id, require_jwt_auth
from app.services import nexora_guard as guard

guard_bp = Blueprint("guard", __name__, url_prefix="/api/v1/guard")


def _user() -> dict:
    u = getattr(request, "user", None) or {}
    return {**u, "workspace_id": get_workspace_id()}


def _db() -> str:
    return str(current_app.config.get("DATABASE_PATH") or "centralized_db.sqlite3")


def _err(msg: str, status: int = 400, code: str = "BAD_REQUEST"):
    return jsonify({"success": False, "error": {"code": code, "message": msg}}), status


@guard_bp.route("/issues", methods=["GET"])
@require_jwt_auth
def list_issues():
    """?status=open|resolved|dismissed|all — checks run again when older than 10 minutes."""
    status = (request.args.get("status") or "open").strip().lower()
    return jsonify({"success": True, "data": guard.issues(_db(), _user(), status)})


@guard_bp.route("/run", methods=["POST"])
@require_jwt_auth
def run_now():
    return jsonify({"success": True, "data": guard.run(_db(), _user())})


@guard_bp.route("/issues/<int:issue_id>/<action>", methods=["POST"])
@require_jwt_auth
def change_issue(issue_id: int, action: str):
    status = {"dismiss": "dismissed", "reopen": "open"}.get(action)
    if not status:
        return _err("Unknown action", 404, "NOT_FOUND")
    try:
        return jsonify({"success": True, "data": guard.set_status(_db(), _user(), issue_id, status)})
    except LookupError as exc:
        return _err(str(exc), 404, "NOT_FOUND")


@guard_bp.route("/issues/<int:issue_id>/explain", methods=["GET"])
@require_jwt_auth
def explain_issue(issue_id: int):
    """✨ Explain & fix — one short AI call."""
    try:
        return jsonify({"success": True, "data": {"text": guard.explain(_db(), _user(), issue_id)}})
    except LookupError as exc:
        return _err(str(exc), 404, "NOT_FOUND")
    except ValueError as exc:
        return _err(str(exc))


@guard_bp.route("/digest", methods=["GET"])
@require_jwt_auth
def daily_digest():
    """Today's AI note (made once a day, then cached)."""
    return jsonify({"success": True, "data": guard.digest(_db(), _user())})

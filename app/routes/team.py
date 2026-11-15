"""Team: duty (Start/End day), live location on duty, and the owner's team view.

Member:  GET/POST /api/v1/team/duty[/start|/end], POST /api/v1/team/location
Owner:   GET /api/v1/team/live, GET /api/v1/team/members/<id>/day?date=YYYY-MM-DD
Only the owner/admin of the same workspace sees a member (404 for anyone else).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.routes.auth import _get_auth_db, get_workspace_id, require_jwt_auth
from app.services import team_tracking as tt

team_bp = Blueprint("team", __name__, url_prefix="/api/v1/team")


def _user() -> dict:
    u = getattr(request, "user", None) or {}
    return {**u, "workspace_id": get_workspace_id()}


def _err(message: str, status: int, code: str = "BAD_REQUEST"):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


@team_bp.route("/duty", methods=["GET"])
@require_jwt_auth
def duty():
    return jsonify({"success": True, "data": tt.duty_status(_get_auth_db().db_path, _user()["user_id"])}), 200


@team_bp.route("/duty/start", methods=["POST"])
@require_jwt_auth
def duty_start():
    """Body: {lat, lng, accuracy, mock} from the phone (needed when the member has work places)."""
    try:
        out = tt.start_duty(_get_auth_db().db_path, _user(), request.get_json(silent=True) or {})
    except tt.NotAtSite as exc:
        return _err(str(exc), 403, "NOT_AT_SITE")
    return jsonify({"success": True, "data": out}), 200


@team_bp.route("/duty/end", methods=["POST"])
@require_jwt_auth
def duty_end():
    """Body: {lat, lng, accuracy, mock}; away from the work place also {note, approver}."""
    try:
        out = tt.end_duty(_get_auth_db().db_path, _user(), request.get_json(silent=True) or {})
    except tt.EndAway as exc:
        return jsonify({"success": False, "error": {"code": "END_AWAY", "message": str(exc),
                                                    "distance_m": exc.distance_m, "site": exc.site_name}}), 403
    return jsonify({"success": True, "data": out}), 200


@team_bp.route("/attendance/me/request", methods=["POST"])
@require_jwt_auth
def my_day_request():
    b = request.get_json(silent=True) or {}
    try:
        out = tt.request_for_day(_get_auth_db().db_path, _user(), str(b.get("date") or ""),
                                 b.get("note"), b.get("approver"))
    except LookupError as exc:
        return _err(str(exc), 404, "NOT_FOUND")
    except ValueError as exc:
        return _err(str(exc), 400)
    return jsonify({"success": True, "data": out}), 200


@team_bp.route("/duty/sites", methods=["GET"])
@require_jwt_auth
def my_sites():
    import sqlite3

    with sqlite3.connect(_get_auth_db().db_path) as conn:
        conn.row_factory = sqlite3.Row
        tt.ensure_schema(conn)
        sites = tt.my_sites(conn, _user()["user_id"])
    return jsonify({"success": True, "data": {"sites": sites}}), 200


@team_bp.route("/attendance/me", methods=["GET"])
@require_jwt_auth
def my_attendance():
    try:
        out = tt.attendance_month(_get_auth_db(), None, (request.args.get("month") or "").strip()
                                  or tt._now().astimezone(tt.IST).strftime("%Y-%m"), self_user=_user())
    except ValueError as exc:
        return _err(str(exc), 400)
    return jsonify({"success": True, "data": out}), 200


@team_bp.route("/location", methods=["POST"])
@require_jwt_auth
def location():
    body = request.get_json(silent=True) or {}
    try:
        out = tt.add_points(_get_auth_db().db_path, _user(), body.get("points") or [])
    except PermissionError as exc:
        return _err(str(exc), 409, "NOT_ON_DUTY")
    except ValueError as exc:
        return _err(str(exc), 400)
    return jsonify({"success": True, "data": out}), 200


@team_bp.route("/live", methods=["GET"])
@require_jwt_auth
def live():
    try:
        members = tt.team_live(_get_auth_db(), _user())
    except PermissionError as exc:
        return _err(str(exc), 403, "FORBIDDEN")
    return jsonify({"success": True, "data": {"members": members}}), 200


@team_bp.route("/members/<int:member_id>/day", methods=["GET"])
@require_jwt_auth
def member_day(member_id: int):
    try:
        out = tt.member_day(_get_auth_db(), _user(), member_id, (request.args.get("date") or "").strip() or None)
    except PermissionError as exc:
        return _err(str(exc), 403, "FORBIDDEN")
    except LookupError as exc:
        return _err(str(exc), 404, "NOT_FOUND")
    except ValueError:
        return _err("date must be YYYY-MM-DD", 400)
    return jsonify({"success": True, "data": out}), 200


# ── Owner: work places, attendance rules and sheet ──────────────────────────

def _guard(fn):
    try:
        return fn()
    except PermissionError as exc:
        return _err(str(exc), 403, "FORBIDDEN")
    except LookupError as exc:
        return _err(str(exc), 404, "NOT_FOUND")
    except ValueError as exc:
        return _err(str(exc), 400)


@team_bp.route("/sites", methods=["GET"])
@require_jwt_auth
def sites():
    return _guard(lambda: (jsonify({"success": True, "data": {"sites": tt.list_sites(_get_auth_db(), _user())}}), 200))


@team_bp.route("/sites", methods=["POST"])
@require_jwt_auth
def save_site():
    return _guard(lambda: (jsonify({"success": True, "data": tt.save_site(
        _get_auth_db(), _user(), request.get_json(silent=True) or {})}), 200))


@team_bp.route("/sites/<int:site_id>", methods=["DELETE"])
@require_jwt_auth
def delete_site(site_id: int):
    def run():
        tt.delete_site(_get_auth_db(), _user(), site_id)
        return jsonify({"success": True, "data": {"deleted": site_id}}), 200
    return _guard(run)


@team_bp.route("/members/<int:member_id>/sites", methods=["POST"])
@require_jwt_auth
def set_member_sites(member_id: int):
    body = request.get_json(silent=True) or {}
    return _guard(lambda: (jsonify({"success": True, "data": {"sites": tt.set_member_sites(
        _get_auth_db(), _user(), member_id, body.get("site_ids") or [])}}), 200))


@team_bp.route("/attendance/rules", methods=["POST"])
@require_jwt_auth
def set_rules():
    body = request.get_json(silent=True) or {}
    return _guard(lambda: (jsonify({"success": True, "data": tt.set_rules(
        _get_auth_db(), _user(), body.get("full_day_hours"), body.get("half_day_hours"))}), 200))


@team_bp.route("/attendance", methods=["GET"])
@require_jwt_auth
def attendance():
    month = (request.args.get("month") or "").strip() or tt._now().astimezone(tt.IST).strftime("%Y-%m")
    member = request.args.get("member_id", type=int)
    return _guard(lambda: (jsonify({"success": True, "data": tt.attendance_month(
        _get_auth_db(), _user(), month, member_id=member)}), 200))


@team_bp.route("/attendance/requests", methods=["GET"])
@require_jwt_auth
def attendance_requests():
    status = request.args.get("status", "pending")
    return _guard(lambda: (jsonify({"success": True, "data": {"requests": tt.list_requests(
        _get_auth_db(), _user(), None if status == "all" else status)}}), 200))


@team_bp.route("/attendance/requests/<int:request_id>", methods=["POST"])
@require_jwt_auth
def decide_attendance_request(request_id: int):
    b = request.get_json(silent=True) or {}
    return _guard(lambda: (jsonify({"success": True, "data": tt.decide_request(
        _get_auth_db(), _user(), request_id, bool(b.get("approve")), b.get("reply"))}), 200))

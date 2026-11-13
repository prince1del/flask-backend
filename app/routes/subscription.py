"""Per-login subscription API.

    GET  /api/v1/subscription/status             my plan: unlimited / trial / active / expired
    POST /api/v1/subscription/redeem             {code} — extend my access

Super admin only:
    POST /api/v1/subscription/codes              {duration_days, count?, for_username?, note?}
    GET  /api/v1/subscription/codes              recent codes and who used them
    POST /api/v1/subscription/codes/<code>/revoke
    GET  /api/v1/subscription/users              every login with its plan
    POST /api/v1/subscription/users/<id>/unlimited
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.routes.auth import _get_auth_db, require_jwt_auth
from app.services import subscriptions as subs

subscription_bp = Blueprint("subscription", __name__, url_prefix="/api/v1/subscription")


def _db_path() -> str:
    return str(_get_auth_db().db_path)


def _user() -> dict:
    user = getattr(request, "user", None)
    return user if isinstance(user, dict) else {}


def _err(message: str, status: int, code: str = "BAD_REQUEST"):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


def _super_admin_only():
    if not subs.is_super_admin(_user()):
        return _err("Only the super admin can manage subscription codes", 403, "FORBIDDEN")
    return None


@subscription_bp.route("/status", methods=["GET"])
@require_jwt_auth
def status():
    user = _user()
    if user.get("user_id") is None:
        return _err("Login required", 401, "UNAUTHORIZED")
    data = subs.status_for_user(_db_path(), int(user["user_id"]))
    if subs.is_super_admin(user):
        data = {**data, "status": "unlimited", "ends_at": None, "days_left": None}
    data["is_super_admin"] = subs.is_super_admin(user)
    return jsonify({"success": True, "data": data}), 200


@subscription_bp.route("/redeem", methods=["POST"])
@require_jwt_auth
def redeem():
    user = _user()
    if user.get("user_id") is None:
        return _err("Login required", 401, "UNAUTHORIZED")
    code = (request.get_json(silent=True) or {}).get("code") or ""
    if not subs.normalize_code(code):
        return _err("Enter the code you received", 400)
    try:
        data = subs.redeem_code(
            _db_path(), int(user["user_id"]), str(user.get("username") or ""), code
        )
    except ValueError as exc:
        return _err(str(exc), 400, "INVALID_CODE")
    return jsonify({"success": True, "data": data}), 200


@subscription_bp.route("/codes", methods=["POST"])
@require_jwt_auth
def create_codes():
    denied = _super_admin_only()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    try:
        codes = subs.generate_codes(
            _db_path(),
            _user(),
            duration_days=data.get("duration_days") or 0,
            count=data.get("count") or 1,
            for_username=data.get("for_username"),
            note=data.get("note"),
        )
    except (TypeError, ValueError) as exc:
        return _err(str(exc), 400)
    return jsonify({"success": True, "data": {"codes": codes}}), 201


@subscription_bp.route("/codes", methods=["GET"])
@require_jwt_auth
def list_codes():
    denied = _super_admin_only()
    if denied:
        return denied
    return jsonify({"success": True, "data": {"codes": subs.list_codes(_db_path())}}), 200


@subscription_bp.route("/codes/<code>/revoke", methods=["POST"])
@require_jwt_auth
def revoke_code(code: str):
    denied = _super_admin_only()
    if denied:
        return denied
    if not subs.revoke_code(_db_path(), code):
        return _err("Code not found, already used or already cancelled", 404, "NOT_FOUND")
    return jsonify({"success": True}), 200


@subscription_bp.route("/users", methods=["GET"])
@require_jwt_auth
def list_users():
    denied = _super_admin_only()
    if denied:
        return denied
    return jsonify({"success": True, "data": {"users": subs.list_user_plans(_db_path())}}), 200


@subscription_bp.route("/users/<int:user_id>/unlimited", methods=["POST"])
@require_jwt_auth
def make_unlimited(user_id: int):
    denied = _super_admin_only()
    if denied:
        return denied
    subs.set_unlimited(_db_path(), user_id)
    return jsonify({"success": True}), 200

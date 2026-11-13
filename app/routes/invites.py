"""Invite teammates into your company (one shared workspace).

    POST   /api/v1/invites                 create a code (company manager)
    GET    /api/v1/invites                 codes of my company + who joined
    POST   /api/v1/invites/<code>/revoke   stop a code
    GET    /api/v1/invites/check?code=…    public: is this code usable, which company
    GET    /api/v1/invites/members         logins in my company

Joining happens in POST /api/v1/signup with ``invite_code``.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.routes.auth import _get_auth_db, get_workspace_id, require_jwt_auth
from app.services import company_invites as ci

invites_bp = Blueprint("invites", __name__, url_prefix="/api/v1/invites")


def _err(message: str, status: int):
    return jsonify({"success": False, "error": {"message": message}}), status


def _user() -> dict:
    user = getattr(request, "user", None)
    return user if isinstance(user, dict) else {}


@invites_bp.route("", methods=["POST"])
@require_jwt_auth
def create_invite():
    db = _get_auth_db()
    user = {**_user(), "workspace_id": get_workspace_id()}
    if not ci.can_manage_invites(db, user):
        return _err("Only your company's owner or admin can invite people", 403)
    data = request.get_json(silent=True) or {}
    try:
        invite = ci.create_invite(
            db, user,
            role=data.get("role") or "sales_executive",
            max_uses=data.get("max_uses") or 1,
            expires_in_days=data.get("expires_in_days") or 7,
            note=data.get("note"),
        )
    except ValueError as exc:
        return _err(str(exc), 400)
    invite["company_name"] = ci.company_name_for_workspace(db, user["workspace_id"])
    return jsonify({"success": True, "data": invite}), 201


@invites_bp.route("", methods=["GET"])
@require_jwt_auth
def list_invites():
    db = _get_auth_db()
    workspace_id = get_workspace_id()
    user = {**_user(), "workspace_id": workspace_id}
    can_manage = ci.can_manage_invites(db, user)
    return jsonify({
        "success": True,
        "data": {
            "can_invite": can_manage,
            "roles": ci.allowed_roles_for(user) if can_manage else [],
            "company_name": ci.company_name_for_workspace(db, workspace_id),
            "invites": ci.list_invites(db, workspace_id) if can_manage else [],
        },
    }), 200


@invites_bp.route("/<code>/revoke", methods=["POST"])
@require_jwt_auth
def revoke_invite(code: str):
    db = _get_auth_db()
    workspace_id = get_workspace_id()
    if not ci.can_manage_invites(db, {**_user(), "workspace_id": workspace_id}):
        return _err("Only your company's owner or admin can stop invites", 403)
    if not ci.revoke_invite(db, workspace_id, code):
        return _err("Invite not found or already stopped", 404)
    return jsonify({"success": True}), 200


@invites_bp.route("/check", methods=["GET"])
def check_invite():
    info = ci.check_invite(_get_auth_db(), request.args.get("code") or "")
    if info is None:
        return jsonify({"success": True, "data": {"valid": False, "status": "not_found"}}), 200
    return jsonify({"success": True, "data": info}), 200


@invites_bp.route("/members", methods=["GET"])
@require_jwt_auth
def list_members():
    db = _get_auth_db()
    workspace_id = get_workspace_id()
    return jsonify({
        "success": True,
        "data": {
            "company_name": ci.company_name_for_workspace(db, workspace_id),
            "members": ci.list_members(db, workspace_id),
        },
    }), 200

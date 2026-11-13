"""Seasons: GET /api/v1/seasons (list + status + CIs without a season),
POST /api/v1/seasons/assign (give an SO a season/category by hand)."""

from __future__ import annotations

import sqlite3

from flask import Blueprint, jsonify, request

from app.routes.auth import get_request_user_id, get_workspace_id, require_jwt_auth

seasons_bp = Blueprint("seasons", __name__, url_prefix="/api/v1/seasons")


def _db():
    from app.routes.data import _db_path
    from centralized_db_system.db import CentralizedDB

    return CentralizedDB(_db_path()), sqlite3.connect(_db_path())


@seasons_bp.route("", methods=["GET"])
@require_jwt_auth
def list_seasons():
    from app.services.seasons import season_overview

    uid = get_request_user_id()
    if uid is None:
        return jsonify({"success": False, "error": {"message": "Authentication required"}}), 401
    db, conn = _db()
    try:
        data = season_overview(db, conn, user_id=uid, workspace_id=str(get_workspace_id() or "default"))
    finally:
        conn.close()
    return jsonify({"success": True, "data": data}), 200


@seasons_bp.route("/assign", methods=["POST"])
@require_jwt_auth
def assign_season():
    from app.services.seasons import assign

    uid = get_request_user_id()
    if uid is None:
        return jsonify({"success": False, "error": {"message": "Authentication required"}}), 401
    body = request.get_json(silent=True) or {}
    _db_obj, conn = _db()
    try:
        out = assign(
            conn,
            workspace_id=str(get_workspace_id() or "default"),
            user_id=uid,
            so_number=str(body.get("so_number") or ""),
            distributor_id=int(body["distributor_id"]) if body.get("distributor_id") not in (None, "") else None,
            season=str(body.get("season") or ""),
            category=str(body.get("category") or ""),
        )
    except (ValueError, TypeError) as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 400
    finally:
        conn.close()
    return jsonify({"success": True, "data": out}), 200

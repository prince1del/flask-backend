"""Distributor Hisaab API — order vs received vs billed per distributor."""

from __future__ import annotations

import sqlite3

from flask import Blueprint, jsonify, request

from app.routes.auth import get_request_user_id, get_workspace_id, require_jwt_auth

distributor_ledger_bp = Blueprint("distributor_ledger", __name__, url_prefix="/api/v1/distributor-ledger")


@distributor_ledger_bp.route("", methods=["GET"])
@require_jwt_auth
def ledger():
    from app.routes.data import _db_path
    from app.services.distributor_ledger import build_ledger
    from centralized_db_system.db import CentralizedDB

    uid = get_request_user_id()
    if uid is None:
        return jsonify({"success": False, "error": {"message": "Authentication required"}}), 401
    conn = sqlite3.connect(_db_path())
    try:
        data = build_ledger(
            CentralizedDB(_db_path()), conn, user_id=uid, workspace_id=str(get_workspace_id() or "default")
        )
    finally:
        conn.close()
    dist_id = request.args.get("distributor_id", type=int)
    if dist_id:
        data["distributors"] = [d for d in data["distributors"] if d["distributor_id"] == dist_id]
    season = (request.args.get("season") or "").strip()
    if season and season.upper() != "ALL":
        from app.services.distributor_ledger import filter_ledger_by_season

        data = filter_ledger_by_season(data, season)
    return jsonify({"success": True, "data": data}), 200

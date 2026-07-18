"""
Company Profile routes — each workspace's own company identity
(name, GST, address, etc.)

Restricted to executive-level roles (admin, sales_executive) only.
Deliberately NOT accessible by distributor/retailer-type logins when
those are introduced later — this represents the EXECUTIVE'S OWN
employer/supplier identity (e.g. Bombay Dyeing's own GST), never a
distributor's own company details.
"""

from flask import Blueprint, request, jsonify, current_app
from app.routes.auth import require_jwt_auth, require_role, get_workspace_id
from centralized_db_system.db import CentralizedDB

company_profile_bp = Blueprint("company_profile", __name__, url_prefix="/api/v1/company-profile")


def _get_db() -> CentralizedDB:
    return CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))


@company_profile_bp.route("", methods=["GET"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def get_company_profile():
    workspace_id = get_workspace_id()
    db = _get_db()
    profile = db.get_company_profile(workspace_id)
    return jsonify({"success": True, "data": profile}), 200


@company_profile_bp.route("", methods=["POST", "PUT"])
@require_jwt_auth
@require_role("admin", "sales_executive")
def save_company_profile():
    payload = request.get_json(silent=True) or {}
    company_name = (payload.get("company_name") or "").strip()
    if not company_name:
        return jsonify({"success": False, "error": {"message": "company_name is required"}}), 400

    workspace_id = get_workspace_id()
    db = _get_db()
    try:
        profile = db.upsert_company_profile(
            workspace_id=workspace_id,
            company_name=company_name,
            gst_number=payload.get("gst_number"),
            pan_number=payload.get("pan_number"),
            address=payload.get("address"),
            city=payload.get("city"),
            state=payload.get("state"),
            pincode=payload.get("pincode"),
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 400

    return jsonify({"success": True, "data": profile}), 200

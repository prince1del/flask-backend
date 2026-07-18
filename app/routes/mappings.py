from flask import Blueprint, jsonify, request, current_app
from app.routes.auth import require_jwt_auth, get_workspace_id
from centralized_db_system.db import CentralizedDB

mappings_bp = Blueprint("mappings", __name__, url_prefix="/api/v1/mappings")


@mappings_bp.route("/material", methods=["GET"])
@require_jwt_auth
def list_mappings():
    workspace_id = get_workspace_id()
    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    items = db.list_material_code_mappings(workspace_id=workspace_id)
    return jsonify({"success": True, "data": items}), 200


@mappings_bp.route("/material", methods=["POST"])
@require_jwt_auth
def create_mapping():
    payload = request.get_json(silent=True) or {}
    code_prefix = (payload.get("code_prefix") or "").strip()
    mapping_type = (payload.get("mapping_type") or "").strip()
    mapping_value = (payload.get("mapping_value") or "").strip()
    description = (payload.get("description") or "").strip() or None
    if not (code_prefix and mapping_type and mapping_value):
        return jsonify({"success": False, "error": {"message": "code_prefix, mapping_type and mapping_value are required"}}), 400
    workspace_id = get_workspace_id()
    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    mid = db.add_material_code_mapping(code_prefix=code_prefix, mapping_type=mapping_type, mapping_value=mapping_value, description=description, workspace_id=workspace_id)
    return jsonify({"success": True, "data": {"id": mid}}), 200


@mappings_bp.route("/material/decode", methods=["POST"])
@require_jwt_auth
def decode_code():
    payload = request.get_json(silent=True) or {}
    code = (payload.get("code") or "").strip()
    if not code:
        return jsonify({"success": False, "error": {"message": "code is required"}}), 400
    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    decoded = db.decode_material_code(code)
    return jsonify({"success": True, "data": decoded}), 200

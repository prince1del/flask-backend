from flask import Blueprint, jsonify, request, current_app

from centralized_db_system.db import CentralizedDB
from app.routes.auth import require_jwt_auth, get_workspace_id

fulfillment_bp = Blueprint("fulfillment", __name__, url_prefix="/api/v1/fulfillment")


@fulfillment_bp.route("/<int:order_lifecycle_id>", methods=["GET"])
@require_jwt_auth
def list_fulfillment(order_lifecycle_id: int):
    workspace_id = get_workspace_id()
    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    items = db.list_fulfillment_items(order_lifecycle_id, workspace_id=workspace_id)
    return jsonify({"success": True, "data": items}), 200


@fulfillment_bp.route("/", methods=["POST"])
@require_jwt_auth
def create_fulfillment():
    payload = request.get_json(silent=True) or {}
    order_lifecycle_id = int(payload.get("order_lifecycle_id") or 0)
    if not order_lifecycle_id:
        return jsonify({"success": False, "error": {"message": "order_lifecycle_id is required"}}), 400
    product_code = (payload.get("product_code") or "").strip() or None
    brand = (payload.get("brand") or "").strip() or None
    color = (payload.get("color") or "").strip() or None
    ordered_qty = int(payload.get("ordered_qty") or 0)

    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    workspace_id = get_workspace_id()
    fid = db.create_order_fulfillment_item(
        order_lifecycle_id=order_lifecycle_id,
        product_code=product_code,
        brand=brand,
        color=color,
        ordered_qty=ordered_qty,
        fulfilled_qty=0,
        workspace_id=workspace_id,
    )
    return jsonify({"success": True, "data": {"id": fid}}), 200


@fulfillment_bp.route("/<int:fulfillment_id>/fulfill", methods=["POST"])
@require_jwt_auth
def fulfill_item(fulfillment_id: int):
    payload = request.get_json(silent=True) or {}
    raw_increment = payload.get("increment")
    try:
        increment = int(raw_increment)
    except (TypeError, ValueError):
        return jsonify(
            {"success": False, "error": {"message": "increment must be an integer"}}
        ), 400
    if increment <= 0:
        return jsonify(
            {
                "success": False,
                "error": {"message": "increment must be greater than zero"},
            }
        ), 400

    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    workspace_id = get_workspace_id()
    try:
        updated = db.update_fulfilled_quantity(
            fulfillment_id,
            fulfilled_increment=increment,
            workspace_id=workspace_id,
        )
    except ValueError as exc:
        message = str(exc)
        status = 404 if "not found" in message.lower() else 400
        return jsonify({"success": False, "error": {"message": message}}), status
    return jsonify({"success": True, "data": updated}), 200

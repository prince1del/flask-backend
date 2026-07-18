import sqlite3

from flask import Blueprint, jsonify, request, current_app

from centralized_db_system.db import CentralizedDB
from app.routes.auth import require_jwt_auth, get_workspace_id

operations_bp = Blueprint("operations", __name__, url_prefix="/api/v1/operations")


@operations_bp.route("/dispatch", methods=["POST"])
@require_jwt_auth
def record_dispatch():
    payload = request.get_json(silent=True) or {}
    tracking_id = int(payload.get("tracking_id") or 0)
    if not tracking_id:
        return jsonify({"success": False, "error": {"message": "tracking_id is required"}}), 400
    pod_number = (payload.get("pod_number") or "").strip() or None
    driver_name = (payload.get("driver_name") or "").strip() or None
    vehicle_number = (payload.get("vehicle_number") or "").strip() or None
    dispatched_at = payload.get("dispatched_at")
    delivered_at = payload.get("delivered_at")

    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    workspace_id = get_workspace_id()
    pid = db.record_dispatch_pod(
        tracking_id=tracking_id,
        pod_number=pod_number,
        driver_name=driver_name,
        vehicle_number=vehicle_number,
        dispatched_at=dispatched_at,
        delivered_at=delivered_at,
        workspace_id=workspace_id,
    )
    lifecycle = db.get_order_lifecycle_tracking(tracking_id)
    return jsonify({"success": True, "data": {"pod_id": pid, "lifecycle": lifecycle}}), 200


@operations_bp.route("/returns", methods=["POST"])
@require_jwt_auth
def create_return():
    payload = request.get_json(silent=True) or {}
    tracking_id = int(payload.get("tracking_id") or 0)
    if not tracking_id:
        return jsonify({"success": False, "error": {"message": "tracking_id is required"}}), 400
    product_code = (payload.get("product_code") or "").strip() or None
    returned_qty = int(payload.get("returned_qty") or 0)
    reason = (payload.get("reason") or "").strip() or None

    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    workspace_id = get_workspace_id()
    cid = db.record_return_claim(
        tracking_id=tracking_id, product_code=product_code, returned_qty=returned_qty, reason=reason, workspace_id=workspace_id
    )
    return jsonify({"success": True, "data": {"claim_id": cid}}), 200


@operations_bp.route("/invoices/reconcile", methods=["POST"])
@require_jwt_auth
def reconcile_invoice():
    payload = request.get_json(silent=True) or {}
    tracking_id = payload.get("tracking_id")
    invoice_number = (payload.get("invoice_number") or "").strip() or None
    invoice_date = payload.get("invoice_date")
    invoice_amount = float(payload.get("invoice_amount") or 0.0)
    reconciled = bool(payload.get("reconciled") or False)
    notes = (payload.get("notes") or "").strip() or None

    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    workspace_id = get_workspace_id()
    rid = db.reconcile_invoice(
        tracking_id=tracking_id,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        invoice_amount=invoice_amount,
        reconciled=reconciled,
        notes=notes,
        workspace_id=workspace_id,
    )
    return jsonify({"success": True, "data": {"reconciliation_id": rid}}), 200


@operations_bp.route("/alerts", methods=["GET"])
@require_jwt_auth
def list_alerts():
    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    workspace_id = get_workspace_id()
    alerts = db.list_alerts(workspace_id=workspace_id)
    return jsonify({"success": True, "data": alerts}), 200


@operations_bp.route("/alerts/<int:alert_id>/resolve", methods=["POST"])
@require_jwt_auth
def resolve_alert(alert_id: int):
    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    workspace_id = get_workspace_id()
    # workspace_id filter is required here — system_alerts.workspace_id
    # exists in the schema, but without filtering on it any
    # authenticated user could resolve any OTHER workspace's alert
    # just by guessing/incrementing alert_id.
    with sqlite3.connect(db.db_path) as conn:
        cursor = conn.execute(
            "UPDATE system_alerts SET resolved = 1 WHERE alert_id = ? AND workspace_id = ?",
            (alert_id, workspace_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return jsonify({"success": False, "error": {"message": "Alert not found"}}), 404
    return jsonify({"success": True, "data": {"alert_id": alert_id, "resolved": True}}), 200

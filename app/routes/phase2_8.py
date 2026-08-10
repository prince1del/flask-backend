from flask import Blueprint, jsonify, request, current_app
from app.routes.auth import require_jwt_auth, get_workspace_id
from centralized_db_system.db import CentralizedDB

phase2_8_bp = Blueprint("phase2_8", __name__, url_prefix="/api/v1/phase2_8")


@phase2_8_bp.route("/pod/attach", methods=["POST"])
@require_jwt_auth
def attach_pod():
    payload = request.get_json(silent=True) or {}
    pod_id = int(payload.get("pod_id") or 0)
    if not pod_id:
        return jsonify({"success": False, "error": {"message": "pod_id required"}}), 400
    pod_text = payload.get("pod_text")
    attachment = payload.get("attachment_reference")
    if isinstance(attachment, str):
        attachment = attachment.strip() or None

    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    workspace_id = get_workspace_id()

    # SECURITY: never Image.open()/OCR a client-supplied filesystem path.
    # That allowed any authenticated user to force the server to read (and
    # return OCR text from) arbitrary images on disk. attachment_reference is
    # opaque metadata only — send pod_text from the client if OCR is needed.
    try:
        result = db.attach_pod_ocr(
            pod_id,
            pod_text=pod_text,
            attachment_reference=attachment,
            workspace_id=workspace_id,
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 404
    return jsonify({"success": True, "data": result}), 200


@phase2_8_bp.route("/invoices/from-reconciliation", methods=["POST"])
@require_jwt_auth
def invoice_from_reconciliation():
    payload = request.get_json(silent=True) or {}
    rec_id = int(payload.get("reconciliation_id") or 0)
    if not rec_id:
        return jsonify({"success": False, "error": {"message": "reconciliation_id required"}}), 400
    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    workspace_id = get_workspace_id()
    inv_id = db.create_invoice_from_reconciliation(rec_id, workspace_id=workspace_id)
    return jsonify({"success": True, "data": {"invoice_id": inv_id}}), 200


@phase2_8_bp.route("/inventory/adjust", methods=["POST"])
@require_jwt_auth
def inventory_adjust():
    payload = request.get_json(silent=True) or {}
    article_code = payload.get("article_code")
    if not article_code:
        return jsonify({"success": False, "error": {"message": "article_code required"}}), 400
    adjustment_qty = float(payload.get("adjustment_qty") or 0.0)
    reason = payload.get("reason")
    related = payload.get("related_tracking_id")

    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    workspace_id = get_workspace_id()
    adj_id = db.apply_inventory_adjustment(article_code, adjustment_qty, reason, related_tracking_id=related, workspace_id=workspace_id)
    return jsonify({"success": True, "data": {"adjustment_id": adj_id}}), 200


@phase2_8_bp.route("/notifications/subscribe", methods=["POST"])
@require_jwt_auth
def notifications_subscribe():
    payload = request.get_json(silent=True) or {}
    target = payload.get("target")
    channel = payload.get("channel")
    address = payload.get("address")
    if not (target and channel and address):
        return jsonify({"success": False, "error": {"message": "target, channel, and address required"}}), 400
    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    workspace_id = get_workspace_id()
    sid = db.create_notification_subscription(target, channel, address, workspace_id=workspace_id)
    return jsonify({"success": True, "data": {"subscription_id": sid}}), 200


@phase2_8_bp.route("/notifications/send", methods=["POST"])
@require_jwt_auth
def notifications_send():
    payload = request.get_json(silent=True) or {}
    target = payload.get("target")
    message = payload.get("message")
    if not (target and message):
        return jsonify({"success": False, "error": {"message": "target and message required"}}), 400
    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    workspace_id = get_workspace_id()
    alert_id = db.send_notification(target, message, workspace_id=workspace_id)
    return jsonify({"success": True, "data": {"alert_id": alert_id}}), 200


@phase2_8_bp.route("/achievements", methods=["POST"])
@require_jwt_auth
def create_achievement():
    payload = request.get_json(silent=True) or {}
    tracking_id = payload.get("tracking_id")
    amount = payload.get("amount")
    if amount is None:
        return jsonify({"success": False, "error": {"message": "amount is required"}}), 400
    try:
        amount = float(amount)
    except Exception:
        return jsonify({"success": False, "error": {"message": "invalid amount"}}), 400

    currency = (payload.get("currency") or "INR").strip()
    notes = payload.get("notes")

    # get created_by if available
    current_user = getattr(request, "user", None)
    created_by = None
    if isinstance(current_user, dict):
        created_by = current_user.get("user_id")

    db = CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))
    workspace_id = get_workspace_id()
    ach_id = db.create_achievement(order_lifecycle_tracking_id=tracking_id, amount=amount, currency=currency, source="ci", created_by=created_by, workspace_id=workspace_id, notes=notes)
    return jsonify({"success": True, "data": {"achievement_id": ach_id}}), 200

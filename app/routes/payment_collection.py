"""Distributor's Payment Collection — SO-wise deposits per distributor.

GET    /api/v1/order-fulfillment/payment-collection
POST   /api/v1/order-fulfillment/payment-collection/entries
DELETE /api/v1/order-fulfillment/payment-collection/entries/<id>
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from app.routes.auth import get_workspace_id, require_jwt_auth
from app.storage.payment_drive_backup import backup_so_payment_collection_to_drive
from centralized_db_system.db import CentralizedDB

payment_collection_bp = Blueprint(
    "payment_collection",
    __name__,
    url_prefix="/api/v1/order-fulfillment/payment-collection",
)


def _db() -> CentralizedDB:
    return CentralizedDB(current_app.config["DATABASE_PATH"])


def _user_id() -> int | None:
    user = getattr(request, "user", None) or {}
    raw = user.get("user_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _username() -> str | None:
    user = getattr(request, "user", None) or {}
    raw = user.get("username") or user.get("email")
    return str(raw).strip() if raw else None


@payment_collection_bp.route("", methods=["GET"])
@require_jwt_auth
def list_payment_collection():
    workspace_id = get_workspace_id()
    distributor_id = request.args.get("distributor_id", type=int)
    distributors = _db().list_distributor_payment_collection(
        workspace_id, distributor_id=distributor_id, user_id=_user_id()
    )
    so_bill_total = round(sum(d.get("so_bill_total") or 0 for d in distributors), 2)
    paid_total = round(sum(d.get("paid_total") or 0 for d in distributors), 2)
    outstanding_total = round(sum(d.get("outstanding_total") or 0 for d in distributors), 2)
    return jsonify(
        {
            "ok": True,
            "distributors": distributors,
            "summary": {
                "distributor_count": len(distributors),
                "so_bill_total": so_bill_total,
                "paid_total": paid_total,
                "outstanding_total": outstanding_total,
            },
        }
    )


@payment_collection_bp.route("/entries", methods=["POST"])
@require_jwt_auth
def create_payment_entry():
    workspace_id = get_workspace_id()
    body = request.get_json(silent=True) or {}
    try:
        distributor_id = int(body.get("distributor_id"))
        tracking_id = int(body.get("tracking_id"))
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "distributor_id, tracking_id and amount are required"}), 400
    payment_date = (body.get("payment_date") or "").strip()
    note = body.get("note")
    try:
        entry = _db().add_distributor_payment_entry(
            workspace_id=workspace_id,
            distributor_id=distributor_id,
            tracking_id=tracking_id,
            amount=amount,
            payment_date=payment_date,
            note=note,
            created_by=_user_id(),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    backup_so_payment_collection_to_drive(
        db=_db(),
        user_id=_user_id(),
        workspace_id=workspace_id,
        username=_username(),
    )
    return jsonify({"ok": True, "entry": entry}), 201


@payment_collection_bp.route("/entries/<int:entry_id>", methods=["DELETE"])
@require_jwt_auth
def delete_payment_entry(entry_id: int):
    workspace_id = get_workspace_id()
    deleted = _db().delete_distributor_payment_entry(entry_id, workspace_id)
    if deleted is None:
        return jsonify({"ok": False, "error": "Payment entry not found"}), 404
    backup_so_payment_collection_to_drive(
        db=_db(),
        user_id=_user_id(),
        workspace_id=workspace_id,
        username=_username(),
    )
    return jsonify({"ok": True, "deleted": deleted})

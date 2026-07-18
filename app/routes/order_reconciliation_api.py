"""Nexora Order Reconciliation API.

Drop this file into: app/routes/order_reconciliation_api.py
Register blueprint in your Flask app factory/main app:
    from app.routes.order_reconciliation_api import order_reconciliation_blueprint
    app.register_blueprint(order_reconciliation_blueprint)

Endpoint:
    POST /api/v1/order-reconciliation/check

Form-data files:
    master_order_file       optional but recommended Excel/CSV
    filled_order_file       required Excel/CSV
    sales_order_file        optional PDF
    commercial_invoice_file optional PDF
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from flask import Blueprint, Response, request

try:
    from app.routes.auth import require_jwt_auth
except Exception:  # pragma: no cover
    def require_jwt_auth(func):  # type: ignore
        return func

from centralized_db_system.order_reconciliation import reconcile_order_chain


order_reconciliation_blueprint = Blueprint("order_reconciliation", __name__)


def _json_response(payload: dict, status: int = 200) -> Response:
    return Response(json.dumps(payload, default=str, indent=2), mimetype="application/json", status=status)


def _save_upload(temp_dir: Path, field_name: str) -> Path | None:
    uploaded = request.files.get(field_name)
    if not uploaded or not uploaded.filename:
        return None
    safe_name = Path(uploaded.filename).name
    target = temp_dir / f"{field_name}_{safe_name}"
    uploaded.save(target)
    return target


@order_reconciliation_blueprint.route("/api/v1/order-reconciliation/check", methods=["POST"])
@require_jwt_auth
def check_order_reconciliation() -> Response:
    """Runs full Master -> Filled Order -> SO -> CI reconciliation."""
    with tempfile.TemporaryDirectory(prefix="nexora_order_recon_") as tmp:
        temp_dir = Path(tmp)
        master_file = _save_upload(temp_dir, "master_order_file")
        filled_file = _save_upload(temp_dir, "filled_order_file")
        so_file = _save_upload(temp_dir, "sales_order_file")
        ci_file = _save_upload(temp_dir, "commercial_invoice_file")

        if filled_file is None:
            return _json_response({"success": False, "error": "filled_order_file is required"}, 400)

        try:
            report = reconcile_order_chain(
                master_booking_form=master_file,
                distributor_filled_order=filled_file,
                sales_order_pdf=so_file,
                commercial_invoice_pdf=ci_file,
            )
        except Exception as exc:
            return _json_response({"success": False, "error": str(exc)}, 400)
        return _json_response(report)

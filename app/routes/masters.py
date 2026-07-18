"""
Master Distributors/Retailers — Bulk Upload & Export routes.

These expose the existing (but previously unreachable — no route ever
called them) CentralizedDB.bulk_upload_masters() /
export_master_distributors_excel() / export_master_retailers_excel()
functions, plus their newly-added CSV variants.

Endpoints:
- POST /api/v1/masters/distributors/bulk-upload
- POST /api/v1/masters/retailers/bulk-upload
- GET  /api/v1/masters/distributors/export?format=xlsx|csv
- GET  /api/v1/masters/retailers/export?format=xlsx|csv&distributor_id=<id>
"""

import tempfile
from pathlib import Path

from flask import Blueprint, request, jsonify, current_app, send_file
from app.routes.auth import require_jwt_auth, get_workspace_id
from centralized_db_system.db import CentralizedDB

masters_bp = Blueprint("masters", __name__, url_prefix="/api/v1/masters")


def _get_db() -> CentralizedDB:
    return CentralizedDB(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3"))


def _save_upload_to_temp(file_storage) -> Path:
    suffix = Path(file_storage.filename or "").suffix or ".xlsx"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    file_storage.save(tmp.name)
    return Path(tmp.name)


@masters_bp.route("/distributors/bulk-upload", methods=["POST"])
@require_jwt_auth
def bulk_upload_distributors():
    if "file" not in request.files:
        return jsonify({"success": False, "error": {"message": "file is required"}}), 400
    workspace_id = get_workspace_id()
    temp_path = _save_upload_to_temp(request.files["file"])
    try:
        db = _get_db()
        result = db.bulk_upload_masters("distributors", temp_path, workspace_id=workspace_id)
        return jsonify({"success": True, "data": result}), 200
    except Exception as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 500
    finally:
        temp_path.unlink(missing_ok=True)


@masters_bp.route("/retailers/bulk-upload", methods=["POST"])
@require_jwt_auth
def bulk_upload_retailers():
    if "file" not in request.files:
        return jsonify({"success": False, "error": {"message": "file is required"}}), 400
    workspace_id = get_workspace_id()
    temp_path = _save_upload_to_temp(request.files["file"])
    try:
        db = _get_db()
        result = db.bulk_upload_masters("retailers", temp_path, workspace_id=workspace_id)
        return jsonify({"success": True, "data": result}), 200
    except Exception as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 500
    finally:
        temp_path.unlink(missing_ok=True)


@masters_bp.route("/distributors", methods=["GET"])
@require_jwt_auth
def list_distributors():
    workspace_id = get_workspace_id()
    limit = request.args.get("limit", 500, type=int)
    db = _get_db()
    distributors = db.list_master_distributors(limit=limit, workspace_id=workspace_id)
    return jsonify({"success": True, "data": distributors}), 200


@masters_bp.route("/distributors", methods=["POST"])
@require_jwt_auth
def create_distributor():
    workspace_id = get_workspace_id()
    payload = request.get_json(silent=True) or {}
    db = _get_db()
    distributor_id = db.add_master_distributor(
        name=payload.get("name") or payload.get("firm_name") or "",
        firm_name=payload.get("firm_name"),
        firm_nick_name=payload.get("firm_nick_name"),
        gst_no=payload.get("gst_no"),
        buyer_code=payload.get("buyer_code"),
        zone=payload.get("zone"),
        region=payload.get("region"),
        location=payload.get("location"),
        address=payload.get("address"),
        pincode=payload.get("pincode"),
        phone_number=payload.get("phone_number"),
        email=payload.get("email"),
        payment_terms=payload.get("payment_terms"),
        birthday=payload.get("birthday"),
        anniversary=payload.get("anniversary"),
        secondary_distributor_name=payload.get("secondary_distributor_name"),
        secondary_distributor_phone_number=payload.get("secondary_distributor_phone_number"),
        secondary_distributor_birthday=payload.get("secondary_distributor_birthday"),
        secondary_distributor_anniversary=payload.get("secondary_distributor_anniversary"),
        sales_executive_name=payload.get("sales_executive_name"),
        sales_executive_phone_number=payload.get("sales_executive_phone_number"),
        sales_executive_email=payload.get("sales_executive_email"),
        sales_executive_birthday=payload.get("sales_executive_birthday"),
        sales_executive_anniversary=payload.get("sales_executive_anniversary"),
        distributor_code=payload.get("distributor_code"),
        credit_limit=payload.get("credit_limit"),
        workspace_id=workspace_id,
    )
    record = db.get_master_distributor(distributor_id, workspace_id=workspace_id)
    if not record:
        return jsonify({"success": False, "error": {"message": "Distributor could not be created"}}), 500
    return jsonify({"success": True, "data": record, "message": "Distributor created successfully"}), 201


@masters_bp.route("/distributors/<int:distributor_id>", methods=["GET"])
@require_jwt_auth
def get_distributor(distributor_id):
    workspace_id = get_workspace_id()
    db = _get_db()
    record = db.get_master_distributor(distributor_id, workspace_id=workspace_id)
    if not record:
        return jsonify({"success": False, "error": {"message": "Distributor not found"}}), 404
    return jsonify({"success": True, "data": record}), 200


@masters_bp.route("/distributors/<int:distributor_id>", methods=["PUT"])
@require_jwt_auth
def update_distributor(distributor_id):
    workspace_id = get_workspace_id()
    payload = request.get_json(silent=True) or {}
    db = _get_db()
    record = db.update_master_distributor(distributor_id, workspace_id, **payload)
    if not record:
        return jsonify({"success": False, "error": {"message": "Distributor not found"}}), 404
    return jsonify({"success": True, "data": record, "message": "Distributor updated successfully"}), 200


@masters_bp.route("/distributors/<int:distributor_id>", methods=["DELETE"])
@require_jwt_auth
def delete_distributor(distributor_id):
    workspace_id = get_workspace_id()
    db = _get_db()
    deleted = db.delete_master_distributor(distributor_id, workspace_id)
    if not deleted:
        return jsonify({"success": False, "error": {"message": "Distributor not found"}}), 404
    return jsonify({"success": True, "data": None, "message": "Distributor deleted successfully"}), 200


@masters_bp.route("/retailers", methods=["GET"])
@require_jwt_auth
def list_retailers():
    workspace_id = get_workspace_id()
    limit = request.args.get("limit", 500, type=int)
    db = _get_db()
    retailers = db.list_master_retailers(limit=limit, workspace_id=workspace_id)
    # Attach a readable distributor name/label to each retailer row —
    # showing a bare internal distributor_id number is not useful in
    # a grid view meant for a real person to read.
    distributors_by_id = {
        d["id"]: (d.get("firm_name") or d.get("name") or "Unnamed Distributor")
        for d in db.list_master_distributors(limit=limit, workspace_id=workspace_id)
    }
    for retailer in retailers:
        did = retailer.get("distributor_id")
        retailer["distributor_name"] = distributors_by_id.get(did, "Unassigned") if did else "Unassigned"
    return jsonify({"success": True, "data": retailers}), 200


@masters_bp.route("/retailers", methods=["POST"])
@require_jwt_auth
def create_retailer():
    workspace_id = get_workspace_id()
    payload = request.get_json(silent=True) or {}
    db = _get_db()
    retailer_id = db.add_master_retailer(
        name=payload.get("name") or "",
        distributor_id=payload.get("distributor_id"),
        location=payload.get("location"),
        phone_number=payload.get("phone_number"),
        phone_number_2=payload.get("phone_number_2"),
        email=payload.get("email"),
        address=payload.get("address"),
        gst_no=payload.get("gst_no"),
        secondary_retailer_name=payload.get("secondary_retailer_name"),
        secondary_retailer_phone_number=payload.get("secondary_retailer_phone_number"),
        secondary_retailer_birthday=payload.get("secondary_retailer_birthday"),
        secondary_retailer_anniversary=payload.get("secondary_retailer_anniversary"),
        sales_executive_name=payload.get("sales_executive_name"),
        sales_executive_phone_number=payload.get("sales_executive_phone_number"),
        sales_executive_email=payload.get("sales_executive_email"),
        sales_executive_birthday=payload.get("sales_executive_birthday"),
        sales_executive_anniversary=payload.get("sales_executive_anniversary"),
        contact_person=payload.get("contact_person"),
        state=payload.get("state"),
        pincode=payload.get("pincode"),
        category=payload.get("category"),
        birthday=payload.get("birthday"),
        anniversary=payload.get("anniversary"),
        workspace_id=workspace_id,
    )
    record = db.get_master_retailer(retailer_id, workspace_id=workspace_id)
    if not record:
        return jsonify({"success": False, "error": {"message": "Retailer could not be created"}}), 500
    return jsonify({"success": True, "data": record, "message": "Retailer created successfully"}), 201


@masters_bp.route("/retailers/<int:retailer_id>", methods=["GET"])
@require_jwt_auth
def get_retailer(retailer_id):
    workspace_id = get_workspace_id()
    db = _get_db()
    record = db.get_master_retailer(retailer_id, workspace_id=workspace_id)
    if not record:
        return jsonify({"success": False, "error": {"message": "Retailer not found"}}), 404
    return jsonify({"success": True, "data": record}), 200


@masters_bp.route("/retailers/<int:retailer_id>", methods=["PUT"])
@require_jwt_auth
def update_retailer(retailer_id):
    workspace_id = get_workspace_id()
    payload = request.get_json(silent=True) or {}
    db = _get_db()
    record = db.update_master_retailer(retailer_id, workspace_id, **payload)
    if not record:
        return jsonify({"success": False, "error": {"message": "Retailer not found"}}), 404
    return jsonify({"success": True, "data": record, "message": "Retailer updated successfully"}), 200


@masters_bp.route("/retailers/<int:retailer_id>", methods=["DELETE"])
@require_jwt_auth
def delete_retailer(retailer_id):
    workspace_id = get_workspace_id()
    db = _get_db()
    deleted = db.delete_master_retailer(retailer_id, workspace_id)
    if not deleted:
        return jsonify({"success": False, "error": {"message": "Retailer not found"}}), 404
    return jsonify({"success": True, "data": None, "message": "Retailer deleted successfully"}), 200


@masters_bp.route("/distributors/export", methods=["GET"])
@require_jwt_auth
def export_distributors():
    workspace_id = get_workspace_id()
    fmt = (request.args.get("format") or "xlsx").strip().lower()
    db = _get_db()

    if fmt == "csv":
        content = db.export_master_distributors_csv(workspace_id=workspace_id)
        mimetype = "text/csv"
        filename = "distributors.csv"
    else:
        content = db.export_master_distributors_excel(workspace_id=workspace_id)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = "distributors.xlsx"

    return send_file(
        __import__("io").BytesIO(content),
        mimetype=mimetype,
        as_attachment=True,
        download_name=filename,
    )


@masters_bp.route("/retailers/export", methods=["GET"])
@require_jwt_auth
def export_retailers():
    workspace_id = get_workspace_id()
    fmt = (request.args.get("format") or "xlsx").strip().lower()
    distributor_id = request.args.get("distributor_id", type=int)
    db = _get_db()

    if fmt == "csv":
        content = db.export_master_retailers_csv(workspace_id=workspace_id, distributor_id=distributor_id)
        mimetype = "text/csv"
        filename = "retailers.csv"
    else:
        content = db.export_master_retailers_excel(workspace_id=workspace_id, distributor_id=distributor_id)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = "retailers.xlsx"

    return send_file(
        __import__("io").BytesIO(content),
        mimetype=mimetype,
        as_attachment=True,
        download_name=filename,
    )

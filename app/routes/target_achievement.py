import os
import tempfile
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

from centralized_db_system.db import CentralizedDB
from app.routes.auth import require_jwt_auth
from app.target_achievement.logic import (
    aggregate_by_distributor,
    aggregate_by_region,
    aggregate_by_state,
    parse_csv_file,
    parse_excel_file,
    validate_amount,
    validate_financial_year,
    update_source_labels,
)


target_achievement_blueprint = Blueprint("target_achievement", __name__)

SUPPORTED_UPLOAD_EXTENSIONS = {".csv", ".xlsx", ".xls", ".xlsm", ".xlsb"}


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        text_value = str(value).replace(",", "")
        return float(text_value)
    except (TypeError, ValueError):
        return None


def _create_temp_upload(file_storage) -> Path:
    filename = file_storage.filename or "upload"
    suffix = Path(filename).suffix.lower() or ".tmp"
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        temp_file.write(file_storage.read())
    finally:
        temp_file.close()
    return Path(temp_file.name)


def _parse_upload_file(file_path: Path) -> list[dict[str, Any]]:
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        return parse_csv_file(str(file_path))
    if suffix in {".xlsx", ".xls", ".xlsm", ".xlsb"}:
        return parse_excel_file(str(file_path))
    raise ValueError("Unsupported upload file type")


def _build_breakup_records(
    fy_id: int, aggregated: dict[str, float], attribute_type: str
) -> list[dict[str, Any]]:
    db = CentralizedDB()
    result = []
    for attribute_name, amount in aggregated.items():
        if amount is None:
            continue
        record_id = db.save_breakup_record(
            fy_id=fy_id,
            attribute_type=attribute_type,
            attribute_name=attribute_name,
            target_amount=None,
            achievement_amount=amount,
            source="Upload",
        )
        result.append(
            {
                "id": record_id,
                "attribute_type": attribute_type,
                "attribute_name": attribute_name,
                "achievement_amount": amount,
            }
        )
    return result


@target_achievement_blueprint.route(
    "/api/v1/target-achievement/financial-years", methods=["GET"]
)
@require_jwt_auth
def list_financial_years() -> tuple[dict[str, Any], int]:
    db = CentralizedDB()
    years = db.get_all_financial_years()
    return {"success": True, "data": years}, 200


@target_achievement_blueprint.route(
    "/api/v1/target-achievement/financial-years", methods=["POST"]
)
@require_jwt_auth
def create_financial_year() -> tuple[dict[str, Any], int]:
    payload = request.get_json(silent=True) or request.form or {}
    financial_year = (payload.get("financial_year") or "").strip()
    is_valid, error = validate_financial_year(financial_year)
    if not is_valid:
        return {"success": False, "error": error}, 400

    target_amount = _safe_float(payload.get("target_amount"))
    achievement_amount = _safe_float(payload.get("achievement_amount"))
    remarks = payload.get("remarks")
    created_by = payload.get("created_by")

    db = CentralizedDB()
    success, record_id, err = db.create_financial_year(
        financial_year=financial_year,
        target_amount=target_amount,
        achievement_amount=achievement_amount,
        remarks=remarks,
        created_by=created_by,
    )
    if not success:
        return {
            "success": False,
            "error": err or "failed_to_create_financial_year",
        }, 400

    return {"success": True, "data": {"id": record_id}}, 201


@target_achievement_blueprint.route(
    "/api/v1/target-achievement/financial-years/<int:fy_id>", methods=["GET"]
)
@require_jwt_auth
def get_financial_year(fy_id: int) -> tuple[dict[str, Any], int]:
    db = CentralizedDB()
    year = db.get_financial_year(fy_id)
    if year is None:
        return {"success": False, "error": "financial_year_not_found"}, 404
    uploads = db.list_upload_records(fy_id)
    breakups = (
        db.get_breakup(fy_id, attribute_type=request.args.get("attribute_type", ""))
        if request.args.get("attribute_type")
        else []
    )
    return {
        "success": True,
        "data": {"year": year, "uploads": uploads, "breakups": breakups},
    }, 200


@target_achievement_blueprint.route(
    "/api/v1/target-achievement/financial-years/<int:fy_id>", methods=["PUT"]
)
@require_jwt_auth
def update_financial_year(fy_id: int) -> tuple[dict[str, Any], int]:
    payload = request.get_json(silent=True) or request.form or {}
    target_amount = _safe_float(payload.get("target_amount"))
    achievement_amount = _safe_float(payload.get("achievement_amount"))
    remarks = payload.get("remarks")

    db = CentralizedDB()
    updated = db.update_financial_year(
        fy_id=fy_id,
        target_amount=target_amount,
        achievement_amount=achievement_amount,
        remarks=remarks,
    )
    if not updated:
        return {
            "success": False,
            "error": "financial_year_not_found_or_not_updated",
        }, 404
    return {"success": True, "data": {"id": fy_id}}, 200


@target_achievement_blueprint.route(
    "/api/v1/target-achievement/financial-years/<int:fy_id>", methods=["DELETE"]
)
@require_jwt_auth
def delete_financial_year(fy_id: int) -> tuple[dict[str, Any], int]:
    db = CentralizedDB()
    deleted = db.delete_financial_year(fy_id)
    if not deleted:
        return {"success": False, "error": "financial_year_not_found"}, 404
    return {"success": True, "data": {"id": fy_id}}, 200


@target_achievement_blueprint.route(
    "/api/v1/target-achievement/financial-years/<int:fy_id>/upload", methods=["POST"]
)
@require_jwt_auth
def upload_financial_year_data(fy_id: int) -> tuple[dict[str, Any], int]:
    file_item = request.files.get("file")
    if file_item is None or file_item.filename == "":
        return {"success": False, "error": "file_required"}, 400

    if Path(file_item.filename).suffix.lower() not in SUPPORTED_UPLOAD_EXTENSIONS:
        return {"success": False, "error": "unsupported_file_type"}, 400

    temp_path = _create_temp_upload(file_item)
    try:
        rows = _parse_upload_file(temp_path)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    if not rows:
        return {"success": False, "error": "empty_upload"}, 400

    total_achievement = sum(float(row.get("amount", 0) or 0) for row in rows)
    total_rows = len(rows)
    upload_id = CentralizedDB().save_upload_record(
        fy_id=fy_id,
        file_name=file_item.filename,
        file_type=Path(file_item.filename).suffix.lower().lstrip("."),
        total_rows=total_rows,
        calculated_total=total_achievement,
        uploaded_by=request.form.get("uploaded_by") or None,
    )

    breakups = []
    breakups.extend(
        _build_breakup_records(fy_id, aggregate_by_distributor(rows), "distributor")
    )
    breakups.extend(_build_breakup_records(fy_id, aggregate_by_state(rows), "state"))
    breakups.extend(_build_breakup_records(fy_id, aggregate_by_region(rows), "region"))

    return {
        "success": True,
        "data": {
            "upload_id": upload_id,
            "file_name": file_item.filename,
            "total_rows": total_rows,
            "total_achievement": total_achievement,
            "breakup_count": len(breakups),
        },
    }, 200


@target_achievement_blueprint.route(
    "/api/v1/target-achievement/financial-years/<int:fy_id>/uploads", methods=["GET"]
)
@require_jwt_auth
def list_financial_year_uploads(fy_id: int) -> tuple[dict[str, Any], int]:
    db = CentralizedDB()
    uploads = db.list_upload_records(fy_id)
    return {"success": True, "data": uploads}, 200


@target_achievement_blueprint.route(
    "/api/v1/target-achievement/financial-years/<int:fy_id>/breakup", methods=["GET"]
)
@require_jwt_auth
def list_financial_year_breakup(fy_id: int) -> tuple[dict[str, Any], int]:
    attribute_type = (request.args.get("attribute_type") or "").strip()
    if not attribute_type:
        return {"success": False, "error": "attribute_type_required"}, 400
    db = CentralizedDB()
    breakups = db.get_breakup(fy_id, attribute_type)
    return {"success": True, "data": breakups}, 200


@target_achievement_blueprint.route(
    "/api/v1/target-achievement/summary", methods=["GET"]
)
@require_jwt_auth
def get_target_achievement_summary() -> tuple[dict[str, Any], int]:
    db = CentralizedDB()
    summary = db.get_target_achievement_summary()
    return {"success": True, "data": summary}, 200

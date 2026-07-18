"""
Order Sheet Master routes — Phase 2.1 NEXORA Order Fulfillment System

Endpoints:
- POST /api/v1/order-sheets — Create order sheet
- GET /api/v1/order-sheets — List order sheets (with optional category filter)
- GET /api/v1/order-sheets/:id — Get single order sheet
- PUT /api/v1/order-sheets/:id/status — Update active status
"""

import hashlib
import os
from pathlib import Path

from flask import Blueprint, request, jsonify, current_app
from app.routes.auth import require_jwt_auth
from centralized_db_system.db import CentralizedDB

order_sheets_bp = Blueprint("order_sheets", __name__, url_prefix="/api/v1/order-sheets")


def _get_current_user():
    user = getattr(request, "user", None)
    if isinstance(user, dict):
        return user
    raise RuntimeError("Authentication required")


def _get_db() -> CentralizedDB:
    configured_db_path = current_app.config.get("DATABASE_PATH")
    if configured_db_path:
        return CentralizedDB(str(configured_db_path))

    env_db_path = os.getenv("DATABASE_PATH")
    if env_db_path:
        return CentralizedDB(str(env_db_path))

    database_url = os.getenv("DATABASE_URL")
    if database_url and database_url.startswith("sqlite://"):
        sqlite_path = database_url.removeprefix("sqlite://")
        if sqlite_path.startswith("/") and len(sqlite_path) >= 3 and sqlite_path[2] == ":":
            sqlite_path = sqlite_path[1:]
        return CentralizedDB(str(sqlite_path))

    return CentralizedDB()


def _fingerprint_file(path: str | None) -> str | None:
    if not path:
        return None
    candidate_path = Path(path)
    if not candidate_path.exists() or not candidate_path.is_file():
        return None

    digest = hashlib.sha256()
    try:
        with candidate_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return None


@order_sheets_bp.route("", methods=["POST"])
@require_jwt_auth
def create_order_sheet():
    """
    Create a new order sheet.
    
    Request body:
    {
        "name": "AW26 Bedsheet",
        "category": "Bedsheet",
        "file_reference": "/uploads/...",
        "is_active": 1
    }
    """
    try:
        current_user = _get_current_user()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 401

    workspace_id = current_user.get("workspace_id", "default")
    data = request.get_json() or {}

    name = str(data.get("name", "") or "").strip()
    category = str(data.get("category", "") or "").strip()
    file_reference_value = data.get("file_reference")
    file_reference = None
    if file_reference_value is not None:
        file_reference = str(file_reference_value).strip() or None

    is_active_value = data.get("is_active", 1)
    is_active = 1 if str(is_active_value).lower() not in {"0", "false", "no", "off", ""} else 0

    if not name:
        return jsonify({"error": "Order sheet name is required"}), 400
    if not category:
        return jsonify({"error": "Category is required"}), 400

    try:
        db = _get_db()
        sheet_id = db.add_order_sheet(
            name=name,
            category=category,
            file_reference=file_reference,
            workspace_id=workspace_id,
            is_active=is_active,
            content_fingerprint=_fingerprint_file(file_reference),
        )
        
        sheet = db.get_order_sheet(sheet_id, workspace_id)
        return jsonify({"data": sheet, "message": "Order sheet created successfully"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@order_sheets_bp.route("", methods=["GET"])
@require_jwt_auth
def list_order_sheets():
    """
    List order sheets for current workspace.
    
    Query params:
    - category: (optional) Filter by category
    - limit: (optional) Number of results (default: 100)
    - offset: (optional) Pagination offset (default: 0)
    """
    try:
        current_user = _get_current_user()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 401

    workspace_id = current_user.get("workspace_id", "default")
    category = request.args.get("category", "").strip() or None
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)

    try:
        db = _get_db()
        sheets = db.list_order_sheets(
            category=category,
            workspace_id=workspace_id,
            limit=limit,
            offset=offset,
        )
        return jsonify({"data": sheets}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@order_sheets_bp.route("/<int:sheet_id>", methods=["GET"])
@require_jwt_auth
def get_order_sheet(sheet_id: int):
    """
    Get a single order sheet by id.
    """
    try:
        current_user = _get_current_user()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 401

    workspace_id = current_user.get("workspace_id", "default")

    try:
        db = _get_db()
        sheet = db.get_order_sheet(sheet_id, workspace_id)
        
        if not sheet:
            return jsonify({"error": "Order sheet not found"}), 404
        
        return jsonify({"data": sheet}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@order_sheets_bp.route("/<int:sheet_id>/status", methods=["PUT"])
@require_jwt_auth
def update_order_sheet_status(sheet_id: int):
    """
    Update the active/inactive status of an order sheet.
    
    Request body:
    {
        "is_active": 0 or 1
    }
    """
    try:
        current_user = _get_current_user()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 401

    workspace_id = current_user.get("workspace_id", "default")
    data = request.get_json() or {}

    is_active = data.get("is_active")
    if is_active is None:
        return jsonify({"error": "is_active field is required"}), 400

    is_active = 1 if is_active else 0

    try:
        db = _get_db()
        updated = db.update_order_sheet_active_status(sheet_id, is_active, workspace_id)
        
        if not updated:
            return jsonify({"error": "Order sheet not found"}), 404
        
        sheet = db.get_order_sheet(sheet_id, workspace_id)
        return jsonify({"data": sheet, "message": "Order sheet status updated successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

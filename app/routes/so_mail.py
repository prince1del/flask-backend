"""SO mail import API — review Bombay Dyeing SO mails before they touch Order Desk / Payment."""

from __future__ import annotations

import sqlite3

from flask import Blueprint, jsonify, request

from app.routes.auth import get_request_user_id, get_workspace_id, require_jwt_auth
from app.services import so_mail_import as smi

so_mail_bp = Blueprint("so_mail", __name__, url_prefix="/api/v1/so-mail")


def _conn() -> sqlite3.Connection:
    from app.routes.data import _db_path

    return sqlite3.connect(_db_path())


def _who():
    uid = get_request_user_id()
    if uid is None:
        return None, None
    return uid, str(get_workspace_id() or "default")


def _auth_error():
    return jsonify({"success": False, "error": {"message": "Authentication required"}}), 401


@so_mail_bp.route("/scan", methods=["POST"])
@require_jwt_auth
def scan():
    uid, ws = _who()
    if uid is None:
        return _auth_error()
    max_messages = request.args.get("max_messages", default=50, type=int) or 50
    try:
        summary = smi.scan_mailbox(uid, ws, max_messages=min(max_messages, 200))
    except Exception as exc:
        return jsonify({"success": False, "error": {"message": f"SO mail scan failed: {exc}"}}), 500
    return jsonify({"success": True, "data": summary}), 200


@so_mail_bp.route("/imports", methods=["GET"])
@require_jwt_auth
def list_imports():
    uid, ws = _who()
    if uid is None:
        return _auth_error()
    conn = _conn()
    try:
        items = smi.list_imports(conn, uid, ws, status=request.args.get("status") or None)
    finally:
        conn.close()
    return jsonify({"success": True, "data": {"items": items}}), 200


@so_mail_bp.route("/imports/<int:import_id>", methods=["GET"])
@require_jwt_auth
def get_import(import_id: int):
    uid, ws = _who()
    if uid is None:
        return _auth_error()
    conn = _conn()
    try:
        item = smi.get_import(conn, uid, ws, import_id)
    finally:
        conn.close()
    if item is None:
        return jsonify({"success": False, "error": {"message": "Not found"}}), 404
    return jsonify({"success": True, "data": item}), 200


@so_mail_bp.route("/imports/<int:import_id>/confirm", methods=["POST"])
@require_jwt_auth
def confirm(import_id: int):
    uid, ws = _who()
    if uid is None:
        return _auth_error()
    body = request.get_json(silent=True) or {}
    overrides = {
        k: body.get(k)
        for k in ("distributor_id", "filled_order_ids", "season", "category",
                  "cd_percent", "tds_applicable", "due_date")
        if k in body
    }
    conn = _conn()
    try:
        result = smi.confirm_import(conn, user_id=uid, workspace_id=ws, import_id=import_id,
                                    overrides=overrides)
    except LookupError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 404
    except ValueError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 400
    except RuntimeError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 409
    finally:
        conn.close()
    return jsonify({"success": True, "data": result}), 200


@so_mail_bp.route("/imports/<int:import_id>/reject", methods=["POST"])
@require_jwt_auth
def reject(import_id: int):
    uid, ws = _who()
    if uid is None:
        return _auth_error()
    conn = _conn()
    try:
        ok = smi.reject_import(conn, user_id=uid, workspace_id=ws, import_id=import_id)
    finally:
        conn.close()
    if not ok:
        return jsonify({"success": False, "error": {"message": "Not found or already handled"}}), 404
    return jsonify({"success": True}), 200

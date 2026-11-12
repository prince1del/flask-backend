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
    try:
        from app.routes.data import _db_path
        from centralized_db_system.db import CentralizedDB

        db = CentralizedDB(_db_path())
        status_once: dict = {}

        def _payment(did, season, cat):
            if "s" not in status_once:
                status_once["s"] = db.list_distributor_category_payment_status(uid) or []
            return smi.current_payment(db, uid, did, season, cat, status=status_once["s"])

        smi.refresh_checks(items, existing_payment=_payment)
        smi.fix_decision_wording(items)
    except Exception:
        pass  # list still works with the stored checks
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
                  "cd_percent", "tds_applicable", "due_date", "answers")
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


@so_mail_bp.route("/imports/<int:import_id>/undo", methods=["POST"])
@require_jwt_auth
def undo(import_id: int):
    uid, ws = _who()
    if uid is None:
        return _auth_error()
    conn = _conn()
    try:
        result = smi.undo_import(conn, user_id=uid, workspace_id=ws, import_id=import_id)
    except LookupError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 404
    except ValueError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 400
    finally:
        conn.close()
    return jsonify({"success": True, "data": result}), 200


@so_mail_bp.route("/summary", methods=["GET"])
@require_jwt_auth
def summary():
    """Small payload for the phone's background check / notifications."""
    uid, ws = _who()
    if uid is None:
        return _auth_error()
    conn = _conn()
    try:
        smi.ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, status, distributor_name, category, subject, updated_at FROM so_mail_imports "
            "WHERE user_id=? AND workspace_id=? AND status IN ('review','auto_saved') "
            "ORDER BY id DESC LIMIT 50",
            (uid, ws),
        ).fetchall()
    finally:
        conn.close()
    items = [{"id": r[0], "status": r[1], "distributor_name": r[2], "category": r[3],
              "subject": r[4], "updated_at": r[5]} for r in rows]
    return jsonify({"success": True, "data": {
        "review_count": sum(1 for i in items if i["status"] == "review"),
        "items": items,
    }}), 200


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

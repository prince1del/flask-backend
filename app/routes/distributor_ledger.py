"""Distributor Hisaab API — order vs received vs billed per distributor."""

from __future__ import annotations

import sqlite3

from flask import Blueprint, jsonify, request

from app.routes.auth import get_request_user_id, get_workspace_id, require_jwt_auth

distributor_ledger_bp = Blueprint("distributor_ledger", __name__, url_prefix="/api/v1/distributor-ledger")


@distributor_ledger_bp.route("", methods=["GET"])
@require_jwt_auth
def ledger():
    from app.routes.data import _db_path
    from app.services.distributor_ledger import build_ledger
    from centralized_db_system.db import CentralizedDB

    uid = get_request_user_id()
    if uid is None:
        return jsonify({"success": False, "error": {"message": "Authentication required"}}), 401
    conn = sqlite3.connect(_db_path())
    try:
        data = build_ledger(
            CentralizedDB(_db_path()), conn, user_id=uid, workspace_id=str(get_workspace_id() or "default")
        )
    finally:
        conn.close()
    dist_id = request.args.get("distributor_id", type=int)
    if dist_id:
        data["distributors"] = [d for d in data["distributors"] if d["distributor_id"] == dist_id]
    season = (request.args.get("season") or "").strip()
    if season and season.upper() != "ALL":
        from app.services.distributor_ledger import filter_ledger_by_season

        data = filter_ledger_by_season(data, season)
    return jsonify({"success": True, "data": data}), 200


# ── Credit notes ─────────────────────────────────────────────────────────────

import re as _re

_TOKEN_RE = _re.compile(r"^cn_[0-9a-f]{32}\.(pdf|jpg|png)$")
_MIMES = {"pdf": "application/pdf", "jpg": "image/jpeg", "png": "image/png"}


def _cn_conn():
    from app.routes.data import _db_path

    return sqlite3.connect(_db_path())


@distributor_ledger_bp.route("/credit-notes/read", methods=["POST"])
@require_jwt_auth
def credit_note_read():
    """Upload a CN PDF/photo: keeps the file and returns what it says (not saved yet)."""
    import uuid

    from app.services import credit_notes as cn

    uid = get_request_user_id()
    if uid is None:
        return jsonify({"success": False, "error": {"message": "Authentication required"}}), 401
    f = request.files.get("file")
    if f is None:
        return jsonify({"success": False, "error": {"message": "Choose a PDF or photo"}}), 400
    name = (f.filename or "").lower()
    if name.endswith(".pdf") or (f.mimetype or "") == "application/pdf":
        ext = "pdf"
    elif name.endswith(".png"):
        ext = "png"
    else:
        ext = "jpg"
    data = f.read()
    if not data:
        return jsonify({"success": False, "error": {"message": "The file is empty"}}), 400
    token = f"cn_{uuid.uuid4().hex}.{ext}"
    (cn.files_dir() / token).write_bytes(data)
    try:
        fields = cn.read_document(data, _MIMES[ext], user_id=uid)
    except ValueError as exc:
        # The file is kept: the user can still type the CN in and attach it.
        return jsonify({"success": True, "data": {"file_token": token, "fields": None,
                                                  "message": str(exc)}}), 200
    return jsonify({"success": True, "data": {"file_token": token, "fields": fields}}), 200


@distributor_ledger_bp.route("/credit-notes", methods=["POST"])
@require_jwt_auth
def credit_note_add():
    from app.services import credit_notes as cn

    uid = get_request_user_id()
    if uid is None:
        return jsonify({"success": False, "error": {"message": "Authentication required"}}), 401
    body = request.get_json(silent=True) or {}
    token = str(body.get("file_token") or "").strip() or None
    if token and (not _TOKEN_RE.match(token) or not (cn.files_dir() / token).is_file()):
        return jsonify({"success": False, "error": {"message": "Upload the file again"}}), 400
    try:
        distributor_id = int(body.get("distributor_id"))
        amount = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": {"message": "Distributor and amount are required"}}), 400
    conn = _cn_conn()
    try:
        entry = cn.add(
            conn,
            workspace_id=str(get_workspace_id() or "default"),
            user_id=uid,
            distributor_id=distributor_id,
            season=str(body.get("season") or ""),
            category=str(body.get("category") or ""),
            amount=amount,
            cn_no=body.get("cn_no"),
            cn_date=body.get("cn_date"),
            reason=body.get("reason"),
            file_name=token,
            created_by=uid,
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 400
    finally:
        conn.close()
    return jsonify({"success": True, "data": {"entry": entry}}), 200


@distributor_ledger_bp.route("/credit-notes/<int:cn_id>", methods=["DELETE"])
@require_jwt_auth
def credit_note_delete(cn_id: int):
    from app.services import credit_notes as cn

    uid = get_request_user_id()
    if uid is None:
        return jsonify({"success": False, "error": {"message": "Authentication required"}}), 401
    conn = _cn_conn()
    try:
        ok = cn.delete(conn, user_id=uid, cn_id=cn_id)
    finally:
        conn.close()
    if not ok:
        return jsonify({"success": False, "error": {"message": "Credit note not found"}}), 404
    return jsonify({"success": True, "data": {"deleted": cn_id}}), 200


@distributor_ledger_bp.route("/credit-notes/<int:cn_id>/file", methods=["GET"])
@require_jwt_auth
def credit_note_file(cn_id: int):
    from flask import send_file

    from app.services import credit_notes as cn

    uid = get_request_user_id()
    if uid is None:
        return jsonify({"success": False, "error": {"message": "Authentication required"}}), 401
    conn = _cn_conn()
    try:
        name = cn.file_name_of(conn, user_id=uid, cn_id=cn_id)
    finally:
        conn.close()
    path = cn.files_dir() / name if name and _TOKEN_RE.match(name) else None
    if path is None or not path.is_file():
        return jsonify({"success": False, "error": {"message": "No file for this credit note"}}), 404
    return send_file(path, mimetype=_MIMES[name.rsplit(".", 1)[1]], max_age=0)


# ── Check a party ledger against NEXORA ─────────────────────────────────────

@distributor_ledger_bp.route("/reconcile", methods=["POST"])
@require_jwt_auth
def reconcile_ledger():
    """Upload the distributor's party ledger (SAP Excel, or PDF/photo read by AI)
    and get every mismatch: missing CI, missing payment, credit note, amounts."""
    from app.routes.data import _db_path
    from app.services import ledger_reconcile as lr
    from app.services.distributor_ledger import build_ledger
    from centralized_db_system.db import CentralizedDB

    uid = get_request_user_id()
    if uid is None:
        return jsonify({"success": False, "error": {"message": "Authentication required"}}), 401
    f = request.files.get("file")
    if f is None:
        return jsonify({"success": False, "error": {"message": "Choose the ledger file"}}), 400
    name = (f.filename or "ledger").lower()
    data = f.read()
    if not data:
        return jsonify({"success": False, "error": {"message": "The file is empty"}}), 400
    try:
        if name.endswith((".xls", ".xlsx", ".csv")):
            from app.services.statement_of_account import _read_sheet_rows, parse_statement_of_account

            try:
                statement = parse_statement_of_account(data, name)
                party, lines, read_by = statement.get("party_name") or "", lr.lines_from_statement(statement), "excel"
                if not lines:
                    raise ValueError("no lines")
            except ValueError:
                # Not the SAP layout NEXORA knows: let AI read the rows.
                if name.endswith(".csv"):
                    import csv
                    import io

                    table = list(csv.reader(io.StringIO(data.decode("utf-8", errors="ignore"))))
                    headers, rows = (table[0] if table else []), table[1:]
                else:
                    headers, rows, _mode = _read_sheet_rows(data, name)
                party, lines = lr.lines_from_sheet(headers, rows, user_id=uid)
                read_by = "ai"
        else:
            mime = "application/pdf" if name.endswith(".pdf") else (f.mimetype or "image/jpeg")
            party, lines = lr.lines_from_document(data, mime, user_id=uid)
            read_by = "ai"
    except ValueError as exc:
        return jsonify({"success": False, "error": {"message": str(exc)}}), 400
    except Exception:
        return jsonify({"success": False, "error": {"message": "Could not read this ledger file"}}), 400

    conn = sqlite3.connect(_db_path())
    try:
        from app.services import fo_so_match_db as matchdb

        matchdb.ensure_schema(conn)  # a fresh workspace has no match tables yet
        ledger = build_ledger(CentralizedDB(_db_path()), conn, user_id=uid,
                              workspace_id=str(get_workspace_id() or "default"))
    finally:
        conn.close()
    dists = ledger.get("distributors") or []
    want = request.form.get("distributor_id", type=int) or request.args.get("distributor_id", type=int)
    dist = next((d for d in dists if d.get("distributor_id") == want), None) if want else \
        lr.match_distributor(party, dists)
    if dist is None:
        return jsonify({"success": True, "data": {
            "need_distributor": True, "party": party,
            "distributors": [{"id": d.get("distributor_id"), "name": d.get("distributor_name")} for d in dists],
        }}), 200
    out = lr.reconcile(lines, dist)
    out.update({"party": party, "read_by": read_by})
    if request.form.get("summary", "1") != "0":
        out["summary"] = lr.summarize(out, user_id=uid)
    return jsonify({"success": True, "data": out}), 200

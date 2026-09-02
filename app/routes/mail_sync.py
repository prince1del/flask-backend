import json

from flask import Blueprint, jsonify, render_template_string, request

from app.routes.auth import get_workspace_id, require_jwt_auth
from app.storage.gmail_oauth import GmailOAuth
from centralized_db_system.db import CentralizedDB

mail_sync_bp = Blueprint("mail_sync", __name__, url_prefix="/api/v1/mail-sync")

_GMAIL_CONNECTED_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gmail Connected</title>
  <style>
    body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
           font-family: system-ui, sans-serif; background:#0b1220; color:#eaf0fb; }
    .card { width:min(420px, 92vw); background:#121c30; border:1px solid rgba(37,224,255,.28);
            border-radius:18px; padding:28px 24px; text-align:center; }
    h1 { margin:0 0 8px; font-size:22px; letter-spacing:.08em; color:#7cf5ff; }
    p { margin:8px 0; color:#c5d0e8; line-height:1.45; }
    a.btn { display:inline-block; margin-top:16px; background:#25e0ff; color:#041018;
            font-weight:700; text-decoration:none; padding:12px 18px; border-radius:12px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>NEXORA</h1>
    <p><strong>Gmail connected.</strong></p>
    <p>Commercial Invoice PDFs in this inbox can now be auto-imported.</p>
    <a class="btn" href="nexora://mail-sync/connected">Return to Nexora</a>
  </div>
  <script>
    if (window.opener) {
      window.opener.postMessage({ type: "gmail_sync_connected" }, "*");
      setTimeout(function() { window.close(); }, 1200);
    } else {
      setTimeout(function() { window.location.href = "nexora://mail-sync/connected"; }, 700);
    }
  </script>
</body>
</html>
"""


def _db() -> CentralizedDB:
    from app.routes.data import _db_path

    return CentralizedDB(_db_path())


def _get_request_user():
    raw_user = getattr(request, "user", None)
    if isinstance(raw_user, dict) and "user_id" in raw_user and "workspace_id" in raw_user:
        return raw_user
    raise RuntimeError("User context not available; authentication required.")


def _gmail_redirect_uri(host: str) -> str:
    env_redirect = GmailOAuth._env("GOOGLE_GMAIL_OAUTH_REDIRECT_URI")
    if env_redirect and "localhost" not in env_redirect and "127.0.0.1" not in env_redirect:
        return env_redirect
    return f"{host}/api/v1/mail-sync/oauth-callback"


@mail_sync_bp.route("/connect", methods=["POST"])
@require_jwt_auth
def connect_gmail():
    try:
        user = _get_request_user()
        host = request.host_url.rstrip("/")
        redirect_uri = _gmail_redirect_uri(host)
        oauth_url, state = GmailOAuth.get_auth_url(
            host_url=host,
            redirect_uri=redirect_uri,
            state_payload={"user_id": user["user_id"], "workspace_id": get_workspace_id()},
        )
        return jsonify(
            {
                "success": True,
                "data": {
                    "oauth_url": oauth_url,
                    "message": "Redirect user to authorize Gmail read-only access",
                    "state": state,
                },
            }
        ), 200
    except ValueError as e:
        return jsonify({"success": False, "error": {"code": "OAUTH_CONFIG", "message": str(e)}}), 400
    except Exception as e:
        return jsonify({"success": False, "error": {"code": "OAUTH_FAILED", "message": str(e)}}), 500


@mail_sync_bp.route("/oauth-callback", methods=["GET"])
def oauth_callback():
    try:
        code = request.args.get("code")
        state = request.args.get("state")
        if not code:
            return render_template_string(
                "<h1>Gmail Connect Failed</h1><p>No authorization code was returned.</p>"
            ), 400

        state_data = GmailOAuth.parse_oauth_state(state)
        user_id = state_data.get("user_id")
        workspace_id = state_data.get("workspace_id") or "default"
        if user_id is None:
            return render_template_string(
                "<h1>Gmail Connect Failed</h1><p>Missing user context in OAuth state.</p>"
            ), 400

        host = request.host_url.rstrip("/")
        token_data = GmailOAuth.exchange_code_for_token(
            auth_code=code,
            host_url=host,
            redirect_uri=_gmail_redirect_uri(host),
        )
        _db().save_storage_account(
            user_id=int(user_id),
            workspace_id=str(workspace_id),
            provider_type="gmail",
            oauth_token=token_data,
            sync_status="connected",
        )
        return render_template_string(_GMAIL_CONNECTED_HTML), 200
    except Exception as e:
        return render_template_string(
            "<h1>Gmail Connect Failed</h1><p>{{ message }}</p>", message=str(e)
        ), 500


@mail_sync_bp.route("/status", methods=["GET"])
@require_jwt_auth
def status():
    try:
        user = _get_request_user()
        db = _db()
        account = db.get_storage_account(
            user_id=user["user_id"], provider_type="gmail", workspace_id=get_workspace_id()
        )
        connected = bool(
            account and account.get("sync_status") == "connected" and account.get("oauth_token")
        )
        return jsonify(
            {
                "success": True,
                "data": {
                    "connected": connected,
                    "last_sync": account.get("last_sync") if account else None,
                },
            }
        ), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@mail_sync_bp.route("/disconnect", methods=["POST"])
@require_jwt_auth
def disconnect():
    try:
        user = _get_request_user()
        disconnected = _db().disconnect_storage_account(
            user_id=user["user_id"],
            workspace_id=get_workspace_id(),
            provider_type="gmail",
        )
        if not disconnected:
            return jsonify({"success": False, "error": "No connected Gmail account found."}), 404
        return jsonify({"success": True, "data": {"message": "Disconnected successfully"}}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@mail_sync_bp.route("/poll", methods=["POST"])
@require_jwt_auth
def poll():
    try:
        import sqlite3

        from app.services.gmail_ci_sync import poll_for_user

        user = _get_request_user()
        max_messages = request.args.get("max_messages", default=15, type=int) or 15
        reset_history = request.args.get("reset", default="false").lower() in ("1", "true", "yes")

        if reset_history:
            db_inst = _db()
            db_inst.ensure_gmail_pending_imports_table()
            with sqlite3.connect(str(db_inst.db_path)) as conn:
                conn.execute(
                    "DELETE FROM gmail_pending_imports WHERE user_id = ? AND workspace_id = ?",
                    (user["user_id"], str(get_workspace_id() or "default")),
                )
                conn.commit()

        summary = poll_for_user(
            user_id=user["user_id"],
            workspace_id=get_workspace_id(),
            max_messages=max_messages,
            reset_history=reset_history,
        )
        return jsonify({"success": True, "data": summary}), 200
    except RuntimeError as e:
        return jsonify(
            {"success": False, "error": {"code": "NOT_CONNECTED", "message": str(e)}}
        ), 400
    except Exception as e:
        return jsonify(
            {"success": False, "error": {"code": "POLL_FAILED", "message": str(e)}}
        ), 500


@mail_sync_bp.route("/log", methods=["GET"])
@require_jwt_auth
def get_import_log():
    try:
        user = _get_request_user()
        limit = request.args.get("limit", default=200, type=int) or 200
        rows = _db().list_gmail_import_log(
            user_id=user["user_id"], workspace_id=get_workspace_id(), limit=limit
        )
        return jsonify({"success": True, "data": {"items": rows}}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@mail_sync_bp.route("/pending", methods=["GET"])
@require_jwt_auth
def list_pending():
    try:
        user = _get_request_user()
        rows = _db().list_gmail_pending_imports(
            user_id=user["user_id"], workspace_id=get_workspace_id(), status="pending"
        )
        enriched = []
        seen_keys = set()
        for r in rows:
            item = dict(r)
            dedup_key = (item.get("kind"), item.get("doc_no") or item.get("filename"))
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            if item.get("preview_json"):
                try:
                    item["preview"] = json.loads(item["preview_json"])
                except Exception:
                    item["preview"] = None
            enriched.append(item)
        return jsonify({"success": True, "data": {"items": enriched}}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@mail_sync_bp.route("/pending/<int:pending_id>/confirm", methods=["POST"])
@require_jwt_auth
def confirm_pending(pending_id: int):
    try:
        from app.routes.data import _confirm_ci_only_impl, _confirm_ci_so_link_impl

        user = _get_request_user()
        body = request.get_json(silent=True) or {}
        distributor_id = body.get("distributor_id")

        db = _db()
        row = db.get_gmail_pending_import(
            pending_id, user_id=user["user_id"], workspace_id=get_workspace_id()
        )
        if not row or row.get("status") != "pending":
            return jsonify(
                {"success": False, "error": {"message": "Pending import not found"}}
            ), 404

        if row.get("kind") != "CI":
            return jsonify(
                {"success": False, "error": {"message": "Only CI pending imports are supported"}}
            ), 400

        preview = json.loads(row.get("preview_json") or "{}")
        if preview.get("no_match_found"):
            if not distributor_id:
                return jsonify(
                    {
                        "success": False,
                        "error": {
                            "code": "DISTRIBUTOR_REQUIRED",
                            "message": "distributor_id is required",
                        },
                    }
                ), 400
            resp = _confirm_ci_only_impl(
                {
                    "order_ref_no": preview.get("order_ref_no"),
                    "invoice_no": preview.get("invoice_no"),
                    "distributor_id": distributor_id,
                    "commercial_invoice_file_reference": preview.get(
                        "commercial_invoice_file_reference"
                    ),
                    "commercial_invoice_parsed": preview.get("commercial_invoice_parsed"),
                    "amount": preview.get("extracted_amount"),
                    "acknowledge_party_mismatch": True,
                }
            )
        else:
            resp = _confirm_ci_so_link_impl(
                {
                    "order_ref_no": preview.get("order_ref_no"),
                    "commercial_invoice_file_reference": preview.get(
                        "commercial_invoice_file_reference"
                    ),
                    "commercial_invoice_parsed": preview.get("commercial_invoice_parsed"),
                    "amount": preview.get("extracted_amount"),
                }
            )
        result = resp.get_json(silent=True) or {}
        if not result.get("success") or (result.get("data") or {}).get("link_error"):
            return jsonify(
                {
                    "success": False,
                    "error": {
                        "message": (result.get("data") or {}).get("link_error")
                        or (result.get("error") or {}).get("message")
                        or "Confirm failed"
                    },
                }
            ), 400

        db.update_gmail_pending_import_status(pending_id, "confirmed")
        return jsonify({"success": True, "data": {"message": "Confirmed"}}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@mail_sync_bp.route("/pending/<int:pending_id>/reject", methods=["POST"])
@require_jwt_auth
def reject_pending(pending_id: int):
    try:
        user = _get_request_user()
        db = _db()
        row = db.get_gmail_pending_import(
            pending_id, user_id=user["user_id"], workspace_id=get_workspace_id()
        )
        if not row:
            return jsonify(
                {"success": False, "error": {"message": "Pending import not found"}}
            ), 404
        db.update_gmail_pending_import_status(pending_id, "rejected")
        return jsonify({"success": True, "data": {"message": "Dismissed"}}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

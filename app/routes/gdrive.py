import base64
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, redirect, request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.db import db
from app.encryption import CredentialEncryption
from app.models import User
from app.business_platform import EventEngine
from app.routes.auth import get_workspace_id, require_jwt_auth
from app.storage.oauth import GoogleDriveOAuth

logger = logging.getLogger(__name__)

gdrive_bp = Blueprint("gdrive", __name__, url_prefix="/api/gdrive")


def _get_current_user():
    """
    Returns the authenticated user dict from request.user.

    Raises RuntimeError if request.user is missing rather than
    silently defaulting to any user_id. Callers (route handlers)
    are responsible for catching this and returning a clean 401.
    """
    user = getattr(request, "user", None)
    if isinstance(user, dict):
        return user
    raise RuntimeError("Authentication required")


def _normalize_user_id(user_id: str):
    return int(user_id) if isinstance(user_id, str) and user_id.isdigit() else user_id


def _get_client_secrets_path() -> str:
    if GoogleDriveOAuth.CLIENT_SECRETS_FILE:
        return GoogleDriveOAuth.CLIENT_SECRETS_FILE
    return str(Path(__file__).resolve().parents[2] / "instance/client_secrets.json")


@gdrive_bp.route("/connect/<user_id>", methods=["GET"])
@require_jwt_auth
def start_gdrive_connect(user_id):
    user_id = _normalize_user_id(user_id)

    # CRITICAL: _get_current_user() can raise RuntimeError if request.user
    # was never set (e.g. AUTH_ENABLED=false and no session/JWT context).
    # This MUST be caught here — letting it propagate as an uncaught
    # exception is not a security fix, it's just a crash. A crash is not
    # the same as a clean, intentional rejection.
    try:
        current_user = _get_current_user()
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 401

    if current_user["user_id"] != user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        workspace_id = get_workspace_id()
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 401

    try:
        redirect_uri = request.host_url.rstrip("/") + "/api/gdrive/callback"
        flow = InstalledAppFlow.from_client_secrets_file(
            _get_client_secrets_path(),
            scopes=GoogleDriveOAuth.SCOPES,
        )
        flow.redirect_uri = redirect_uri

        state_data = f"{user_id}:{workspace_id}:{int(datetime.utcnow().timestamp())}"
        state = base64.b64encode(state_data.encode()).decode()

        auth_uri, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            state=state,
        )

        logger.info(f"User {user_id} initiated GDrive OAuth")
        return jsonify({"auth_uri": auth_uri, "user_id": user_id})
    except Exception as e:
        logger.error(f"OAuth flow error: {e}")
        return jsonify({"error": str(e)}), 500


@gdrive_bp.route("/callback", methods=["GET"])
def gdrive_callback():
    """OAuth callback used by Cloud Hub (must match Google Console redirect URI)."""
    from flask import render_template_string
    from centralized_db_system.db import CentralizedDB

    try:
        auth_code = request.args.get("code")
        state = request.args.get("state")
        error = request.args.get("error")

        if error:
            return render_template_string(
                "<h1>Google Drive Connect Failed</h1><p>{{ message }}</p>",
                message=f"OAuth error: {error}",
            ), 400
        if not state or not auth_code:
            return render_template_string(
                "<h1>Google Drive Connect Failed</h1><p>Missing authorization code or state.</p>"
            ), 400

        # Cloud Hub sends JSON state; older flow used user:workspace:timestamp
        parsed = GoogleDriveOAuth.parse_oauth_state(state)
        if parsed.get("user_id") is not None:
            user_id = parsed["user_id"]
            workspace_id = (
                parsed.get("workspace_id")
                or parsed.get("work_id")
                or "default"
            )
        else:
            try:
                state_data = base64.b64decode(state).decode()
                parts = state_data.split(":")
                if len(parts) != 3:
                    raise ValueError("Invalid state payload")
                user_id, workspace_id, _timestamp = parts
            except Exception as decode_error:
                return render_template_string(
                    "<h1>Google Drive Connect Failed</h1><p>{{ message }}</p>",
                    message=f"Invalid state: {decode_error}",
                ), 400

        user_id = int(user_id)
        workspace_id = str(workspace_id or "default")
        redirect_uri = GoogleDriveOAuth._resolve_redirect_uri(
            host_url=request.host_url,
        )
        token_data = GoogleDriveOAuth.exchange_code_for_token(
            auth_code=auth_code,
            host_url=request.host_url,
            redirect_uri=redirect_uri,
        )

        # Persist for Cloud Hub (/api/v1/storage/account)
        cdb = CentralizedDB()
        cdb.ensure_storage_tables()
        cdb.save_storage_account(
            user_id=user_id,
            workspace_id=workspace_id,
            provider_type="google_drive",
            oauth_token=token_data,
            sync_status="connected",
        )

        # Best-effort legacy users.gdrive_* sync. Never fail the OAuth
        # callback if the SQLAlchemy users schema is behind (e.g. missing email).
        google_email = ""
        try:
            from google.oauth2.credentials import Credentials

            credentials = Credentials.from_authorized_user_info(token_data)
            drive_service = build("drive", "v3", credentials=credentials)
            profile = drive_service.about().get(fields="user(emailAddress)").execute()
            google_email = profile.get("user", {}).get("emailAddress", "") or ""
        except Exception:
            logger.warning("Unable to resolve Google Drive user email after OAuth connect")

        try:
            encryption = CredentialEncryption()
            access_enc = encryption.encrypt(token_data.get("token") or "")
            refresh_enc = encryption.encrypt(token_data.get("refresh_token") or "")
            # Raw SQL avoids ORM column mismatches (users.email, updated_at, …).
            db_path = str(cdb.db_path)
            with sqlite3.connect(db_path) as conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
                sets = []
                params: list = []
                if "gdrive_access_token" in cols:
                    sets.append("gdrive_access_token = ?")
                    params.append(access_enc)
                if "gdrive_refresh_token" in cols:
                    sets.append("gdrive_refresh_token = ?")
                    params.append(refresh_enc)
                if "gdrive_connected" in cols:
                    sets.append("gdrive_connected = 1")
                if "gdrive_email" in cols and google_email:
                    sets.append("gdrive_email = ?")
                    params.append(google_email)
                if sets:
                    params.append(user_id)
                    conn.execute(
                        f"UPDATE users SET {', '.join(sets)} WHERE id = ?",
                        params,
                    )
                    conn.commit()
        except Exception as legacy_error:
            logger.warning(
                "Legacy users.gdrive_* sync skipped after storage connect: %s",
                legacy_error,
            )

        try:
            EventEngine.emit(
                "gdrive_connected",
                workspace_id,
                {"user_id": user_id, "google_email": google_email},
            )
        except Exception as emit_error:
            logger.warning("gdrive_connected event emit skipped: %s", emit_error)

        return render_template_string(
            "<!doctype html><html><head><meta charset='utf-8'><title>Google Drive Connected</title></head>"
            "<body><h1>Google Drive Connected</h1>"
            "<p>You can close this window and return to NEXORA Cloud Hub.</p>"
            "<script>"
            "if (window.opener) {"
            "  window.opener.postMessage({ type: 'google_drive_connected' }, '*');"
            "  setTimeout(function(){ window.close(); }, 1200);"
            "}"
            "</script></body></html>"
        ), 200

    except Exception as e:
        logger.error(f"Unexpected error in callback: {e}", exc_info=True)
        return render_template_string(
            "<!doctype html><html><head><meta charset='utf-8'><title>Google Drive Connect Failed</title></head>"
            "<body><h1>Google Drive Connect Failed</h1><p>{{ message }}</p>"
            "<script>"
            "if (window.opener) {"
            "  window.opener.postMessage({ type: 'google_drive_connection_failed', message: {{ message|tojson }} }, '*');"
            "}"
            "</script></body></html>",
            message=str(e),
        ), 500


@gdrive_bp.route("/status/<user_id>", methods=["GET"])
@require_jwt_auth
def get_gdrive_status(user_id):
    user_id = _normalize_user_id(user_id)

    try:
        current_user = _get_current_user()
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 401

    if current_user["user_id"] != user_id:
        return jsonify({"error": "Unauthorized"}), 401

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    return jsonify({
        "is_connected": bool(user.gdrive_connected),
        "email": user.gdrive_email or None,
        "user_id": user_id,
    })


@gdrive_bp.route("/disconnect/<user_id>", methods=["POST"])
@require_jwt_auth
def disconnect_gdrive(user_id):
    user_id = _normalize_user_id(user_id)

    try:
        current_user = _get_current_user()
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 401

    if current_user["user_id"] != user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        user.gdrive_access_token = None
        user.gdrive_refresh_token = None
        user.gdrive_connected = False
        user.gdrive_email = None
        db.session.commit()

        EventEngine.emit(
            "gdrive_disconnected",
            current_user.get("workspace_id", "default"),
            {"user_id": user_id},
        )

        logger.info(f"User {user_id} disconnected GDrive")
        return jsonify({"status": "disconnected"})
    except Exception as e:
        logger.error(f"Disconnect error: {e}")
        return jsonify({"error": str(e)}), 500

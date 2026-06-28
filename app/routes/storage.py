import json
from flask import Blueprint, jsonify, redirect, request, session, url_for

from app.routes.auth import require_jwt_auth
from app.storage.manager import StorageManager
from app.storage.oauth import GoogleDriveOAuth
from app.storage.providers.google_drive_provider import GoogleDriveProvider

bp = Blueprint("storage", __name__)
storage_manager = StorageManager()
storage_manager.register_provider("google_drive", GoogleDriveProvider)


def _get_current_user_id() -> int:
    user = getattr(request, "user", None)
    if isinstance(user, dict) and user.get("user_id"):
        return int(user["user_id"])
    return int(session.get("user_id", 1))


@bp.route("/api/v1/storage/connect", methods=["POST"])
@require_jwt_auth
def connect_storage():
    auth_url, state = GoogleDriveOAuth.get_auth_url()
    session["google_oauth_state"] = state
    return jsonify({"auth_url": auth_url, "state": state})


@bp.route("/api/v1/storage/oauth-callback", methods=["GET"])
def oauth_callback():
    error = request.args.get("error")
    if error:
        return jsonify({"success": False, "error": error}), 400

    state = request.args.get("state")
    if state != session.get("google_oauth_state"):
        return jsonify({"success": False, "error": "Invalid OAuth state"}), 400

    code = request.args.get("code")
    if not code:
        return jsonify({"success": False, "error": "Missing authorization code"}), 400

    credentials = GoogleDriveOAuth.exchange_code_for_token(code)
    user_id = _get_current_user_id()
    result = storage_manager.connect_user_storage(
        user_id=user_id, provider_type="google_drive", oauth_token=credentials
    )
    return jsonify({"success": True, "data": result}), 200


@bp.route("/api/v1/storage/disconnect", methods=["POST"])
@require_jwt_auth
def disconnect_storage():
    user_id = _get_current_user_id()
    result = storage_manager.disconnect_user_storage(user_id)
    return jsonify(result)


@bp.route("/api/v1/storage/account", methods=["GET"])
@require_jwt_auth
def storage_account():
    user_id = _get_current_user_id()
    account = storage_manager.get_storage_account(user_id)
    if account is None:
        return jsonify({"success": False, "error": "no_connected_storage"}), 404
    return jsonify({"success": True, "data": account}), 200


@bp.route("/api/v1/storage/dashboard", methods=["GET"])
@require_jwt_auth
def storage_dashboard():
    user_id = _get_current_user_id()
    dashboard = storage_manager.get_storage_dashboard(user_id)
    return jsonify(dashboard)


@bp.route("/api/v1/storage/sync", methods=["POST"])
@require_jwt_auth
def sync_storage():
    user_id = _get_current_user_id()
    incremental = request.args.get("incremental", "true").lower() != "false"
    result = storage_manager.sync_user_storage(user_id, incremental=incremental)
    return jsonify(result)


@bp.route("/api/v1/storage/files", methods=["GET"])
@require_jwt_auth
def list_storage_files():
    user_id = _get_current_user_id()
    folder_id = request.args.get("folder_id", "")
    files = storage_manager.list_files(user_id, folder_id)
    return jsonify({"files": files})


@bp.route("/api/v1/files/search", methods=["GET"])
@require_jwt_auth
def search_files():
    user_id = _get_current_user_id()
    query = request.args.get("q", "")
    filters = request.args.to_dict(flat=True)
    result = storage_manager.search_files(user_id, query, filters)
    return jsonify(result)

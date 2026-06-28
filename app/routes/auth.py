from functools import wraps

from flask import Blueprint, Response, current_app, jsonify, redirect, render_template_string, request, session, url_for

from centralized_db_system.db import CentralizedDB
from app.jwt_service import JWTService
from app.utils import auth_enabled

auth_blueprint = Blueprint("auth", __name__)

LOGIN_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>NEXORA |Sign In</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; }
    form { max-width: 320px; display: grid; gap: 0.75rem; }
    input { padding: 0.6rem; }
    button { padding: 0.6rem 1rem; }
    .error { color: #b91c1c; }
  </style>
</head>
<body>
  <h1>Sign in</h1>
  <form method="post">
    <input name="username" placeholder="Username" required />
    <input name="password" type="password" placeholder="Password" required />
    <button type="submit">Sign In</button>
  </form>
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
</body>
</html>
"""


def register_auth_hooks(app) -> None:
    app.before_request(enforce_auth)
    app.before_request(ensure_default_admin)


def get_jwt_service() -> JWTService:
    service = current_app.extensions.get("jwt_service")
    if service is None:
        service = JWTService(secret_key=current_app.config.get("SECRET_KEY", "change-me"))
        current_app.extensions["jwt_service"] = service
    return service


def require_jwt_auth(fn):
    @wraps(fn)
    def decorated(*args, **kwargs):
        if not auth_enabled():
            return fn(*args, **kwargs)

        if request.path.startswith("/api/") or request.headers.get("Authorization") or request.is_json:
            return get_jwt_service().require_auth(fn)(*args, **kwargs)

        if session.get("authenticated"):
            return fn(*args, **kwargs)

        return redirect(url_for("auth.login"))

    return decorated


def enforce_auth() -> Response | None:
    if not auth_enabled():
        return None

    if request.path in {"/login", "/logout", "/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/logout"}:
        return None
    if request.path.startswith("/api/"):
        return None
    if request.path.startswith("/static/"):
        return None
    if request.path in {"/manifest.json", "/service-worker.js", "/icon-192.svg", "/icon-512.svg"}:
        return None

    if session.get("authenticated"):
        return None

    return redirect(url_for("auth.login"))


def ensure_default_admin() -> None:
    if not auth_enabled():
        return
    CentralizedDB().ensure_default_admin_user()


@auth_blueprint.route("/api/v1/auth/login", methods=["POST"], endpoint="api_login")
def api_login() -> tuple[Response, int]:
    data = request.get_json(silent=True) or request.form or {}
    username = (data.get("username") if isinstance(data, dict) else "").strip()
    password = data.get("password") if isinstance(data, dict) else ""

    if not username or not password:
        return jsonify({"success": False, "error": {"code": "MISSING_CREDENTIALS", "message": "Username and password required"}}), 400

    db = CentralizedDB()
    if not db.authenticate_user(username, password):
        return jsonify({"success": False, "error": {"code": "INVALID_CREDENTIALS", "message": "Invalid username or password"}}), 401

    service = get_jwt_service()
    access_token, refresh_token = service.create_tokens(
        user_id=1,
        username=username,
        role="admin",
        workspace_id="default",
    )
    return jsonify({
        "success": True,
        "data": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": service.access_token_expiry,
            "token_type": "Bearer",
            "user": {"username": username, "role": "admin", "workspace_id": "default"},
        },
    }), 200


@auth_blueprint.route("/api/v1/auth/refresh", methods=["POST"], endpoint="api_refresh")
def api_refresh() -> tuple[Response, int]:
    data = request.get_json(silent=True) or {}
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        return jsonify({"success": False, "error": {"code": "NO_REFRESH_TOKEN", "message": "Refresh token required"}}), 400

    payload = get_jwt_service().verify_token(refresh_token)
    if "error" in payload:
        return jsonify({"success": False, "error": {"code": "INVALID_REFRESH_TOKEN", "message": payload["error"]}}), 401

    if payload.get("type") != "refresh":
        return jsonify({"success": False, "error": {"code": "INVALID_TOKEN_TYPE", "message": "Not a refresh token"}}), 401

    access_token, _ = get_jwt_service().create_tokens(
        user_id=payload.get("user_id", 1),
        username=payload.get("username", "admin"),
        role=payload.get("role", "admin"),
        workspace_id=payload.get("workspace_id", "default"),
    )
    return jsonify({"success": True, "data": {"access_token": access_token, "expires_in": get_jwt_service().access_token_expiry, "token_type": "Bearer"}}), 200


@auth_blueprint.route("/api/v1/auth/logout", methods=["POST"], endpoint="api_logout")
def api_logout() -> tuple[Response, int]:
    return jsonify({"success": True, "data": {"message": "Logged out successfully"}}), 200


@auth_blueprint.route("/login", methods=["GET", "POST"], endpoint="login")
def login() -> str | Response:
    if not auth_enabled():
        session["authenticated"] = True
        return redirect("/")

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if CentralizedDB().authenticate_user(username, password):
            session["authenticated"] = True
            session["username"] = username
            return redirect(request.args.get("next") or "/")
        error = "Invalid username or password"

    return render_template_string(LOGIN_TEMPLATE, error=error)


@auth_blueprint.route("/logout", endpoint="logout")
def logout() -> Response:
    session.clear()
    return redirect(url_for("auth.login"))

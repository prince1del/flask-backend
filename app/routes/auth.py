import sqlite3
import uuid
from functools import wraps

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    render_template_string,
    request,
    session,
    url_for,
)

from centralized_db_system.db import CentralizedDB
from app.jwt_service import JWTService, session_is_current
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
        secret_key = current_app.config.get("SECRET_KEY")
        if not secret_key:
            raise RuntimeError(
                "SECRET_KEY must be configured in app configuration before JWT service initialization."
            )
        service = JWTService(secret_key=secret_key)
        current_app.extensions["jwt_service"] = service
    return service


def require_jwt_auth(fn):
    @wraps(fn)
    def decorated(*args, **kwargs):
        if not auth_enabled():
            return fn(*args, **kwargs)

        # An explicit Authorization header always takes precedence —
        # this is a genuine token-based caller (mobile app, external
        # API client, etc.) and must be verified as a real JWT.
        if request.headers.get("Authorization"):
            return get_jwt_service().require_auth(fn)(*args, **kwargs)

        # CRITICAL FIX: a valid browser session (from the HTML /login
        # form) is just as trustworthy as a JWT for THIS SAME browser's
        # own requests — including calls to /api/* endpoints. Before
        # this fix, the /api/ path check below ran FIRST and ALWAYS
        # demanded a JWT for any /api/* route, even when the caller
        # already had a valid, authenticated session — meaning every
        # fetch() call made by the frontend's own JavaScript (app.js)
        # against /api/* routes (e.g. /api/v1/target-achievement/years,
        # /api/v1/storage/account) failed with 401, because nothing in
        # the browser-form login flow ever issues/stores a JWT Bearer
        # token for app.js to attach — it only sets a session cookie.
        # This silently broke entire dashboard sections (e.g. the
        # "Customers" tab, which fetches its data this way) for every
        # real browser user, while all our JWT-based automated tests
        # kept passing.
        if session.get("authenticated"):
            uid = session.get("user_id")
            is_owner = bool(session.get("is_workspace_owner"))
            if not is_owner and uid is not None:
                try:
                    is_owner = _get_auth_db().is_workspace_owner_user(uid)
                except Exception:
                    is_owner = False
            request.user = {
                "user_id": uid,
                "username": session.get("username"),
                "role": session.get("role", "unassigned"),
                "workspace_id": session.get("workspace_id", "default"),
                "is_workspace_owner": is_owner,
            }
            return fn(*args, **kwargs)

        # No session, no Authorization header — genuine API-style
        # requests must supply a valid JWT; browser page routes get
        # redirected to the login form instead.
        if request.path.startswith("/api/") or request.is_json:
            return get_jwt_service().require_auth(fn)(*args, **kwargs)

        # Prefer JSON for XHR/fetch callers hitting non-/api routes (e.g. legacy /search)
        # so the SPA does not try to parse the HTML login page as JSON.
        wants_json = "application/json" in (request.headers.get("Accept") or "")
        if wants_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return (
                jsonify(
                    {
                        "success": False,
                        "error": {
                            "code": "UNAUTHORIZED",
                            "message": "Authentication required",
                        },
                    }
                ),
                401,
            )

        return redirect(url_for("auth.login"))

    return decorated


def require_role(*allowed_roles):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not auth_enabled():
                # No identity exists at all in this mode, so there is no role
                # to check — matching require_jwt_auth, which also steps aside.
                return fn(*args, **kwargs)
            user = getattr(request, "user", None)
            if not isinstance(user, dict):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": {
                                "code": "FORBIDDEN",
                                "message": "Insufficient permissions",
                            },
                        }
                    ),
                    403,
                )
            role = user.get("role")
            if role in allowed_roles:
                return fn(*args, **kwargs)
            # Supreme workspace owner may use admin-gated APIs while keeping BD role.
            if user.get("is_workspace_owner") and (
                "admin" in allowed_roles or "hop_admin" in allowed_roles
            ):
                return fn(*args, **kwargs)
            return (
                jsonify(
                    {
                        "success": False,
                        "error": {
                            "code": "FORBIDDEN",
                            "message": "Insufficient permissions",
                        },
                    }
                ),
                403,
            )

        return wrapped

    return decorator


def is_request_workspace_owner() -> bool:
    user = getattr(request, "user", None)
    if isinstance(user, dict) and user.get("is_workspace_owner"):
        return True
    uid = get_request_user_id()
    if uid is None:
        return False
    try:
        return _get_auth_db().is_workspace_owner_user(uid)
    except Exception:
        return False


def enforce_auth() -> Response | None:
    if not auth_enabled():
        return None

    if request.path in {
        "/login",
        "/logout",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
    }:
        return None
    if request.path.startswith("/api/"):
        return None
    if request.headers.get("Authorization") or request.is_json:
        return None
    if request.path.startswith("/static/"):
        return None
    if request.path in {
        "/manifest.json",
        "/service-worker.js",
        "/icon-192.svg",
        "/icon-512.svg",
    }:
        return None

    if session.get("authenticated"):
        return None

    return redirect(url_for("auth.login"))


def _get_auth_db() -> CentralizedDB:
    configured_db_path = current_app.config.get("DATABASE_PATH")
    if configured_db_path:
        return CentralizedDB(str(configured_db_path))
    return CentralizedDB()


def ensure_default_admin() -> None:
    if not auth_enabled():
        return
    _get_auth_db().ensure_default_admin_user()


def get_workspace_id() -> str:
    """Data silo for this request — always from auth token/session, never from client body/query."""
    user = getattr(request, 'user', None)
    if isinstance(user, dict) and user.get('workspace_id'):
        return str(user['workspace_id'])

    if not auth_enabled():
        return "default"

    raise RuntimeError(
        "Workspace ID cannot be determined from request parameters; authentication is required."
    )


def get_request_user_id() -> int | None:
    user = getattr(request, "user", None)
    if not isinstance(user, dict):
        return None
    raw = user.get("user_id", user.get("id"))
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def get_user_row(username: str) -> dict[str, object] | None:
    db = _get_auth_db()
    db.ensure_user_profile_columns()
    conn = sqlite3.connect(str(db.db_path))
    conn.row_factory = sqlite3.Row
    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        select_columns = ["id", "username", "password_hash"]
        for col in ("role", "workspace_id", "email", "full_name", "phone", "status", "is_workspace_owner"):
            if col in columns:
                select_columns.append(col)
        row = db.resolve_user_login_row(conn, username, select_columns)
        data = dict(row) if row is not None else None
        if data is not None and "role" not in data:
            data["role"] = "unassigned"
        if data is not None and "workspace_id" not in data:
            data["workspace_id"] = "default"
        if data is not None:
            data["is_workspace_owner"] = bool(int(data.get("is_workspace_owner") or 0))
        return data
    finally:
        conn.close()


@auth_blueprint.route("/api/v1/auth/login", methods=["POST"], endpoint="api_login")
def api_login() -> tuple[Response, int]:
    data = request.get_json(silent=True) or request.form or {}
    username = (data.get("username") if isinstance(data, dict) else "").strip()
    password = data.get("password") if isinstance(data, dict) else ""

    if not username or not password:
        return (
            jsonify(
                {
                    "success": False,
                    "error": {
                        "code": "MISSING_CREDENTIALS",
                        "message": "Username and password required",
                    },
                }
            ),
            400,
        )

    db = _get_auth_db()
    user_row = get_user_row(username)
    if not user_row or not db.authenticate_user(username, password):
        return (
            jsonify(
                {
                    "success": False,
                    "error": {
                        "code": "INVALID_CREDENTIALS",
                        "message": "Invalid username or password",
                    },
                }
            ),
            401,
        )

    service = get_jwt_service()
    is_owner = bool(user_row.get("is_workspace_owner"))
    user_id = user_row.get("id", 1)
    # One live device per account: this login takes over and the previous
    # device's tokens stop working on their next call. Owner is exempt.
    session_id = uuid.uuid4().hex
    device_label = None
    if isinstance(data, dict):
        device_label = (data.get("device_label") or data.get("device_id") or None)
    try:
        db.set_active_session(user_id, session_id, device_label)
    except Exception:
        session_id = None
    access_token, refresh_token = service.create_tokens(
        user_id=user_id,
        username=user_row.get("username", username),
        role=user_row.get("role", "unassigned"),
        workspace_id=user_row.get("workspace_id", "default"),
        is_workspace_owner=is_owner,
        session_id=session_id,
    )
    ui_theme = db.get_user_ui_theme(user_row.get("id"))
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_in": service.access_token_expiry,
                    "token_type": "Bearer",
                    "user": {
                        "id": user_row.get("id", 1),
                        "username": user_row.get("username", username),
                        "email": user_row.get("email"),
                        "full_name": user_row.get("full_name"),
                        "phone": user_row.get("phone"),
                        "role": user_row.get("role", "unassigned"),
                        "workspace_id": user_row.get("workspace_id", "default"),
                        "is_workspace_owner": is_owner,
                        "ui_theme": ui_theme,
                    },
                },
            }
        ),
        200,
    )


@auth_blueprint.route("/api/v1/auth/set-recovery-pin", methods=["POST"], endpoint="set_recovery_pin")
@require_jwt_auth
def set_recovery_pin() -> tuple[Response, int]:
    """Deprecated — recovery PIN replaced by secret question + email reset."""
    return (
        jsonify(
            {
                "success": False,
                "error": {
                    "code": "DEPRECATED",
                    "message": (
                        "Recovery PIN is retired. Set a secret question in Settings, "
                        "or use email reset from the login screen."
                    ),
                },
            }
        ),
        410,
    )


@auth_blueprint.route("/api/v1/auth/recovery-pin-status", methods=["GET"], endpoint="recovery_pin_status")
@require_jwt_auth
def recovery_pin_status() -> tuple[Response, int]:
    user_id = get_request_user_id()
    if user_id is None:
        return jsonify({"success": False, "error": {"code": "NO_USER", "message": "User id missing"}}), 401
    has_secret = _get_auth_db().has_secret_question(user_id)
    return jsonify({
        "success": True,
        "data": {
            "has_recovery_pin": False,
            "has_secret_question": has_secret,
        },
    }), 200


@auth_blueprint.route("/api/v1/auth/secret-questions", methods=["GET"], endpoint="list_secret_questions")
def list_secret_questions() -> tuple[Response, int]:
    from centralized_db_system.db import CentralizedDB

    return jsonify({
        "success": True,
        "data": {"questions": list(CentralizedDB.SECRET_QUESTIONS)},
    }), 200


@auth_blueprint.route("/api/v1/auth/secret-question-hint", methods=["POST"], endpoint="secret_question_hint")
def secret_question_hint() -> tuple[Response, int]:
    data = request.get_json(silent=True) or {}
    login = str(data.get("username") or data.get("login") or "").strip()
    question = _get_auth_db().get_secret_question_for_login(login) if login else None
    return jsonify({
        "success": True,
        "data": {"secret_question": question},
    }), 200


@auth_blueprint.route("/api/v1/me/secret-question", methods=["PUT"], endpoint="set_my_secret_question")
@require_jwt_auth
def set_my_secret_question() -> tuple[Response, int]:
    user_id = get_request_user_id()
    if user_id is None:
        return jsonify({"success": False, "error": {"code": "NO_USER", "message": "User id missing"}}), 401
    data = request.get_json(silent=True) or {}
    try:
        _get_auth_db().set_secret_question(
            user_id,
            str(data.get("secret_question") or ""),
            str(data.get("secret_answer") or ""),
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": {"code": "INVALID", "message": str(exc)}}), 400
    profile = _get_auth_db().get_user_profile(user_id)
    return jsonify({"success": True, "data": profile, "message": "Secret question saved"}), 200


# Assignable by workspace owner from the team screen. Literal "admin" is
# retired — platform power is solely is_workspace_owner on WORKSPACE_OWNER_USERNAME.
_ALLOWED_WORKSPACE_ROLES = {"sales_executive", "distributor", "retailer", "unassigned", "hop_admin"}


@auth_blueprint.route("/api/v1/workspace/users", methods=["GET"], endpoint="list_workspace_users")
@require_jwt_auth
def list_workspace_users() -> tuple[Response, int]:
    requester = getattr(request, "user", None) or {}
    if not requester.get("is_workspace_owner"):
        return (
            jsonify(
                {
                    "success": False,
                    "error": {"code": "FORBIDDEN", "message": "Only the workspace owner can manage users"},
                }
            ),
            403,
        )
    workspace_id = requester.get("workspace_id", "default")
    q = (request.args.get("q") or "").strip() or None
    role = (request.args.get("role") or "").strip().lower() or None
    if role and role not in _ALLOWED_WORKSPACE_ROLES:
        role = None
    page = request.args.get("page", default=1, type=int)
    page_size = request.args.get("page_size", default=25, type=int)
    payload = _get_auth_db().list_workspace_users(
        workspace_id,
        owner_scope=True,
        q=q,
        role=role,
        page=page,
        page_size=page_size,
    )
    return jsonify({"success": True, "data": payload}), 200


@auth_blueprint.route(
    "/api/v1/workspace/users/<int:target_user_id>/role",
    methods=["PUT"],
    endpoint="update_workspace_user_role",
)
@require_jwt_auth
def update_workspace_user_role(target_user_id: int) -> tuple[Response, int]:
    requester = getattr(request, "user", None) or {}
    if not requester.get("is_workspace_owner"):
        return (
            jsonify(
                {
                    "success": False,
                    "error": {"code": "FORBIDDEN", "message": "Only the workspace owner can manage users"},
                }
            ),
            403,
        )
    data = request.get_json(silent=True) or {}
    role = str(data.get("role") or "").strip()
    if role not in _ALLOWED_WORKSPACE_ROLES:
        return jsonify({"success": False, "error": {"code": "INVALID_ROLE", "message": "Invalid role"}}), 400
    workspace_id = requester.get("workspace_id", "default")
    try:
        updated = _get_auth_db().update_user_role(
            target_user_id, workspace_id, role, owner_scope=True
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": str(exc)}}), 404
    return jsonify({"success": True, "data": updated}), 200


@auth_blueprint.route("/api/v1/auth/forgot-password", methods=["POST"], endpoint="forgot_password")
def forgot_password() -> tuple[Response, int]:
    """Reset password via secret answer (preferred) or legacy recovery PIN."""
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "")
    new_password = str(data.get("new_password") or "")
    secret_answer = str(data.get("secret_answer") or "").strip()
    pin = str(data.get("pin") or "").strip()

    if secret_answer:
        ok, message = _get_auth_db().reset_password_with_secret(
            username, secret_answer, new_password
        )
        if not ok:
            return jsonify({"success": False, "error": {"code": "RESET_FAILED", "message": message}}), 400
        return jsonify({"success": True, "message": message}), 200

    if pin:
        ok, message = _get_auth_db().reset_password_with_pin(username, pin, new_password)
        if not ok:
            return jsonify({"success": False, "error": {"code": "RESET_FAILED", "message": message}}), 400
        return jsonify({"success": True, "message": message}), 200

    return jsonify({
        "success": False,
        "error": {
            "code": "RESET_FAILED",
            "message": "Provide secret_answer (or request an email code)",
        },
    }), 400


@auth_blueprint.route(
    "/api/v1/auth/request-password-reset",
    methods=["POST"],
    endpoint="request_password_reset",
)
def request_password_reset() -> tuple[Response, int]:
    """Email a 6-digit reset code when SMTP is configured."""
    from app.services.outbound_mail import send_plain_email, smtp_configured

    data = request.get_json(silent=True) or {}
    login = str(data.get("username") or data.get("email") or data.get("login") or "").strip()
    if not smtp_configured():
        return jsonify({
            "success": False,
            "error": {
                "code": "EMAIL_UNAVAILABLE",
                "message": (
                    "Email reset is not configured on the server yet. "
                    "Use your secret question on the login screen, or ask an admin."
                ),
            },
        }), 503

    ok, message, code, email = _get_auth_db().issue_password_reset_code(login)
    if not ok:
        return jsonify({"success": False, "error": {"code": "RESET_FAILED", "message": message}}), 400
    if code and email:
        sent, send_err = send_plain_email(
            to=email,
            subject="Nexora password reset code",
            body=(
                f"Your Nexora password reset code is: {code}\n\n"
                f"It expires in 15 minutes. If you did not request this, ignore this email.\n"
            ),
        )
        if not sent:
            return jsonify({
                "success": False,
                "error": {"code": "EMAIL_FAILED", "message": f"Could not send email: {send_err}"},
            }), 502
    # Always generic when we intentionally hide enumeration
    return jsonify({
        "success": True,
        "message": "If that account has an email on file, a reset code was sent.",
        "data": {"email_hint": _mask_email(email) if email else None},
    }), 200


@auth_blueprint.route(
    "/api/v1/auth/reset-password-with-code",
    methods=["POST"],
    endpoint="reset_password_with_code",
)
def reset_password_with_code() -> tuple[Response, int]:
    data = request.get_json(silent=True) or {}
    login = str(data.get("username") or data.get("email") or data.get("login") or "")
    code = str(data.get("code") or data.get("reset_code") or "")
    new_password = str(data.get("new_password") or "")
    ok, message = _get_auth_db().reset_password_with_code(login, code, new_password)
    if not ok:
        return jsonify({"success": False, "error": {"code": "RESET_FAILED", "message": message}}), 400
    return jsonify({"success": True, "message": message}), 200


def _mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        masked_local = local[:1] + "*"
    else:
        masked_local = local[:1] + "*" * (len(local) - 2) + local[-1:]
    return f"{masked_local}@{domain}"


@auth_blueprint.route("/api/v1/me/delete-account", methods=["POST"], endpoint="delete_my_account")
@require_jwt_auth
def delete_my_account() -> tuple[Response, int]:
    """Self-service account deletion — requires current password confirmation.

    Deletes only the login row (+ UI prefs). Does not wipe HoP / other users'
    business tables.
    """
    import os

    user_id = get_request_user_id()
    if user_id is None:
        return jsonify({"success": False, "error": {"code": "NO_USER", "message": "User id missing"}}), 401
    data = request.get_json(silent=True) or {}
    password = str(data.get("password") or "")
    if not password:
        return jsonify({
            "success": False,
            "error": {"code": "PASSWORD_REQUIRED", "message": "Password is required to delete your account"},
        }), 400

    cdb = _get_auth_db()
    profile = cdb.get_user_profile(int(user_id))
    if not profile:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "User not found"}}), 404

    username = str(profile.get("username") or "")
    founder = (os.getenv("WORKSPACE_OWNER_USERNAME") or "kunwar1del").strip() or "kunwar1del"
    if username.lower() == founder.lower():
        return jsonify({
            "success": False,
            "error": {"code": "FORBIDDEN", "message": "Founder account cannot be deleted"},
        }), 403
    if (
        str(profile.get("workspace_id") or "") == "house_of_prizm"
        and str(profile.get("role") or "") == "hop_admin"
    ):
        return jsonify({
            "success": False,
            "error": {
                "code": "FORBIDDEN",
                "message": "House of Prizm admin login cannot be self-deleted",
            },
        }), 403

    if not cdb.authenticate_user(username, password):
        return jsonify({
            "success": False,
            "error": {"code": "BAD_PASSWORD", "message": "Incorrect password"},
        }), 401

    try:
        deleted = cdb.delete_login_user(int(user_id))
    except ValueError as exc:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": str(exc)}}), 404

    return jsonify({
        "success": True,
        "data": deleted,
        "message": "Your account has been deleted",
    }), 200


@auth_blueprint.route("/api/v1/me/profile", methods=["GET"])
@require_jwt_auth
def get_my_profile() -> tuple[Response, int]:
    user_id = get_request_user_id()
    if user_id is None:
        return jsonify({"success": False, "error": {"code": "NO_USER", "message": "User id missing"}}), 401
    profile = _get_auth_db().get_user_profile(user_id)
    if profile is None:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "User not found"}}), 404
    return jsonify({"success": True, "data": profile}), 200


@auth_blueprint.route("/api/v1/me/profile", methods=["PUT"])
@require_jwt_auth
def put_my_profile() -> tuple[Response, int]:
    user_id = get_request_user_id()
    if user_id is None:
        return jsonify({"success": False, "error": {"code": "NO_USER", "message": "User id missing"}}), 401
    data = request.get_json(silent=True) or {}
    try:
        profile = _get_auth_db().update_user_profile(
            user_id,
            username=data.get("username") if "username" in data else None,
            email=data.get("email") if "email" in data else None,
            full_name=data.get("full_name") if "full_name" in data else None,
            phone=data.get("phone") if "phone" in data else None,
            employee_id=data.get("employee_id") if "employee_id" in data else None,
            password=data.get("password") if "password" in data else None,
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": {"code": "INVALID", "message": str(exc)}}), 400
    except Exception as exc:
        current_app.logger.exception("put_my_profile failed")
        return (
            jsonify(
                {
                    "success": False,
                    "error": {"code": "SERVER_ERROR", "message": f"Profile save failed: {exc}"},
                }
            ),
            500,
        )
    return jsonify({"success": True, "data": profile, "message": "Profile updated"}), 200


@auth_blueprint.route("/api/v1/me/ui-theme", methods=["GET"])
@require_jwt_auth
def get_my_ui_theme() -> tuple[Response, int]:
    user_id = get_request_user_id()
    if user_id is None:
        return jsonify({"success": False, "error": {"code": "NO_USER", "message": "User id missing"}}), 401
    prefs = _get_auth_db().get_user_ui_theme(user_id)
    return jsonify({"success": True, "data": prefs}), 200


@auth_blueprint.route("/api/v1/me/ui-theme", methods=["PUT"])
@require_jwt_auth
def put_my_ui_theme() -> tuple[Response, int]:
    user_id = get_request_user_id()
    if user_id is None:
        return jsonify({"success": False, "error": {"code": "NO_USER", "message": "User id missing"}}), 401
    data = request.get_json(silent=True) or {}
    theme = data.get("theme") or data.get("theme_id") or "emerald"
    colors = data.get("custom_colors")
    try:
        prefs = _get_auth_db().set_user_ui_theme(user_id, theme, colors if isinstance(colors, dict) else None)
    except ValueError as exc:
        return jsonify({"success": False, "error": {"code": "INVALID_THEME", "message": str(exc)}}), 400
    return jsonify({"success": True, "data": prefs}), 200


@auth_blueprint.route("/api/v1/auth/refresh", methods=["POST"], endpoint="api_refresh")
def api_refresh() -> tuple[Response, int]:
    data = request.get_json(silent=True) or {}
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        return (
            jsonify(
                {
                    "success": False,
                    "error": {
                        "code": "NO_REFRESH_TOKEN",
                        "message": "Refresh token required",
                    },
                }
            ),
            400,
        )

    payload = get_jwt_service().verify_token(refresh_token)
    if "error" in payload:
        return (
            jsonify(
                {
                    "success": False,
                    "error": {
                        "code": "INVALID_REFRESH_TOKEN",
                        "message": payload["error"],
                    },
                }
            ),
            401,
        )

    if payload.get("type") != "refresh":
        return (
            jsonify(
                {
                    "success": False,
                    "error": {
                        "code": "INVALID_TOKEN_TYPE",
                        "message": "Not a refresh token",
                    },
                }
            ),
            401,
        )

    is_owner = bool(
        payload.get("is_workspace_owner")
        or _get_auth_db().is_workspace_owner_user(payload.get("user_id"))
    )
    payload["is_workspace_owner"] = is_owner
    if not session_is_current(payload):
        return (
            jsonify(
                {
                    "success": False,
                    "error": {
                        "code": "SESSION_REVOKED",
                        "message": "Signed out — this account was opened on another device.",
                    },
                }
            ),
            401,
        )

    access_token, _ = get_jwt_service().create_tokens(
        user_id=payload.get("user_id", 1),
        username=payload.get("username", "admin"),
        role=payload.get("role", "unassigned"),
        workspace_id=payload.get("workspace_id", "default"),
        is_workspace_owner=is_owner,
        session_id=payload.get("sid"),
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "access_token": access_token,
                    "expires_in": get_jwt_service().access_token_expiry,
                    "token_type": "Bearer",
                },
            }
        ),
        200,
    )


@auth_blueprint.route("/api/v1/auth/logout", methods=["POST"], endpoint="api_logout")
def api_logout() -> tuple[Response, int]:
    # Best-effort release of the device slot so the next login is instant.
    token = None
    header = request.headers.get("Authorization") or ""
    if header.startswith("Bearer "):
        token = header.split(" ", 1)[1].strip()
    if not token:
        data = request.get_json(silent=True) or {}
        token = data.get("refresh_token") or data.get("access_token")
    if token:
        payload = get_jwt_service().verify_token(token)
        if isinstance(payload, dict) and "error" not in payload and payload.get("sid"):
            try:
                _get_auth_db().clear_active_session(
                    int(payload.get("user_id")), str(payload.get("sid"))
                )
            except Exception:
                pass
    return (
        jsonify({"success": True, "data": {"message": "Logged out successfully"}}),
        200,
    )


@auth_blueprint.route("/login", methods=["GET", "POST"], endpoint="login")
def login() -> str | Response:
    if not auth_enabled():
        session["authenticated"] = True
        return redirect("/")

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        # Use _get_auth_db() (Flask-config based) instead of a bare
        # CentralizedDB() (env-var based) — these two can resolve to
        # DIFFERENT database files depending on how DATABASE_PATH is
        # configured, which is exactly the kind of inconsistency that
        # caused this session-based login to silently authenticate
        # against the wrong database in testing.
        if _get_auth_db().authenticate_user(username, password):
            user_row = get_user_row(username)
            session["authenticated"] = True
            session["username"] = username
            # CRITICAL: without storing role/workspace_id here, every
            # route that relies on get_workspace_id() will crash with
            # "Workspace ID cannot be determined..." for anyone who logs
            # in through this browser form (as opposed to the JSON API
            # login) — that gap only surfaced during real-world browser
            # testing, since all our automated tests used the JWT/API
            # login path, which sets request.user correctly.
            session["user_id"] = user_row.get("id") if user_row else None
            session["role"] = user_row.get("role", "unassigned") if user_row else "unassigned"
            session["workspace_id"] = user_row.get("workspace_id", "default") if user_row else "default"
            session["is_workspace_owner"] = bool(
                user_row.get("is_workspace_owner") if user_row else False
            )
            return redirect(request.args.get("next") or "/")
        error = "Invalid username or password"

    return render_template("index.html", error=error)


@auth_blueprint.route("/logout", endpoint="logout")
def logout() -> Response:
    session.clear()
    return redirect(url_for("auth.login"))

"""
Authentication Routes for NEXORA v1.1
JWT-based login, refresh, logout
"""

from flask import Blueprint, request, jsonify
from werkzeug.security import check_password_hash
from datetime import datetime

from centralized_db_system.db import CentralizedDB

bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")


def init_jwt_service(app):
    """Initialize JWT service with app secret key"""
    from jwt_service import JWTService

    jwt_service = JWTService(
        secret_key=app.config.get("SECRET_KEY", "your-secret-key-change-in-production")
    )
    return jwt_service


@bp.route("/login", methods=["POST"])
def login():
    """Login with username + password, get JWT tokens"""
    try:
        data = request.get_json()
        username = data.get("username")
        password = data.get("password")

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

        db = CentralizedDB()
        user_row = db._db_path and None
        with db._db_path.open("r"):
            pass
        conn = None
        import sqlite3

        conn = sqlite3.connect(str(db._db_path))
        conn.row_factory = sqlite3.Row
        user_row = conn.execute(
            "SELECT id, username, password_hash, role, workspace_id FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        conn.close()

        if not user_row or not check_password_hash(user_row["password_hash"], password):
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

        # Create tokens
        from jwt_service import JWTService

        jwt_service = JWTService(secret_key="your-secret-key-change-in-production")
        access_token, refresh_token = jwt_service.create_tokens(
            user_id=user_row["id"],
            username=user_row["username"],
            role=user_row["role"],
            workspace_id=user_row["workspace_id"],
        )

        return (
            jsonify(
                {
                    "success": True,
                    "data": {
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "expires_in": jwt_service.access_token_expiry,
                        "token_type": "Bearer",
                        "user": {
                            "id": user_row["id"],
                            "username": user_row["username"],
                            "role": user_row["role"],
                            "workspace_id": user_row["workspace_id"],
                        },
                    },
                }
            ),
            200,
        )

    except Exception as e:
        return (
            jsonify(
                {"success": False, "error": {"code": "LOGIN_ERROR", "message": str(e)}}
            ),
            500,
        )


@bp.route("/refresh", methods=["POST"])
def refresh():
    """Refresh access token using refresh token"""
    try:
        data = request.get_json()
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

        # Verify refresh token
        from jwt_service import JWTService

        jwt_service = JWTService(secret_key="your-secret-key-change-in-production")
        payload = jwt_service.verify_token(refresh_token)

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

        # Get user
        db = CentralizedDB()
        conn = sqlite3.connect(str(db._db_path))
        conn.row_factory = sqlite3.Row
        user_row = conn.execute(
            "SELECT id, username, password_hash, role, workspace_id FROM users WHERE username = ?",
            (payload["username"],),
        ).fetchone()
        conn.close()

        if not user_row:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": {
                            "code": "USER_NOT_FOUND",
                            "message": "User not found",
                        },
                    }
                ),
                404,
            )

        # Create new access token
        new_access_token, _ = jwt_service.create_tokens(
            user_id=user_row["id"],
            username=user_row["username"],
            role=user_row["role"],
            workspace_id=user_row["workspace_id"],
        )

        return (
            jsonify(
                {
                    "success": True,
                    "data": {
                        "access_token": new_access_token,
                        "expires_in": jwt_service.access_token_expiry,
                        "token_type": "Bearer",
                    },
                }
            ),
            200,
        )

    except Exception as e:
        return (
            jsonify(
                {
                    "success": False,
                    "error": {"code": "REFRESH_ERROR", "message": str(e)},
                }
            ),
            500,
        )


@bp.route("/logout", methods=["POST"])
def logout():
    """Logout (client-side token deletion)"""
    # JWT is stateless, so logout is client-side token deletion
    # Server doesn't need to do anything
    return (
        jsonify({"success": True, "data": {"message": "Logged out successfully"}}),
        200,
    )

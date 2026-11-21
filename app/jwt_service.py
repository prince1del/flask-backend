"""
JWT Service for NEXORA
Handles token creation, verification, and authentication
"""

import jwt
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import request, jsonify


class JWTService:
    def __init__(self, secret_key, algorithm="HS256"):
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.access_token_expiry = 3600  # 1 hour
        # Slides forward on every refresh, so a sign-in in use lasts until sign-out.
        self.refresh_token_expiry = 86400 * 60  # 60 days of no use

    def create_tokens(
        self,
        user_id,
        username,
        role,
        workspace_id,
        is_workspace_owner: bool = False,
        session_id: str | None = None,
    ):
        """Create access token + refresh token"""
        now = datetime.now(timezone.utc)

        # Access token payload
        access_payload = {
            "user_id": user_id,
            "username": username,
            "role": role,
            "workspace_id": workspace_id,
            "is_workspace_owner": bool(is_workspace_owner),
            "sid": session_id,
            "iat": now,
            "exp": now + timedelta(seconds=self.access_token_expiry),
            "type": "access",
        }

        # Refresh token payload
        refresh_payload = {
            "user_id": user_id,
            "username": username,
            "role": role,
            "workspace_id": workspace_id,
            "is_workspace_owner": bool(is_workspace_owner),
            "sid": session_id,
            "iat": now,
            "exp": now + timedelta(seconds=self.refresh_token_expiry),
            "type": "refresh",
        }

        access_token = jwt.encode(
            access_payload, self.secret_key, algorithm=self.algorithm
        )
        refresh_token = jwt.encode(
            refresh_payload, self.secret_key, algorithm=self.algorithm
        )

        return access_token, refresh_token

    def verify_token(self, token):
        """Verify and decode token"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload
        except jwt.ExpiredSignatureError:
            return {"error": "Session timeout"}
        except jwt.InvalidTokenError:
            return {"error": "Invalid session"}

    def require_auth(self, f):
        """Decorator to protect endpoints"""

        @wraps(f)
        def decorated_function(*args, **kwargs):
            token = None

            # Extract token from Authorization header
            if "Authorization" in request.headers:
                auth_header = request.headers["Authorization"]
                try:
                    token = auth_header.split(" ")[1]  # "Bearer <token>"
                except IndexError:
                    return (
                        jsonify(
                            {
                                "success": False,
                                "error": {
                                    "code": "INVALID_HEADER",
                                    "message": "Invalid authorization header",
                                },
                            }
                        ),
                        401,
                    )

            if not token:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": {
                                "code": "NO_TOKEN",
                                "message": "Missing authorization token",
                            },
                        }
                    ),
                    401,
                )

            # Verify token
            payload = self.verify_token(token)
            if "error" in payload:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": {
                                "code": "INVALID_TOKEN",
                                "message": payload["error"],
                            },
                        }
                    ),
                    401,
                )

            # Attach user to request context
            request.user = payload
            # Enrich owner flag from DB so older JWTs still get supreme powers
            # after promote_workspace_owner runs on deploy.
            if not payload.get("is_workspace_owner"):
                try:
                    from centralized_db_system.db import CentralizedDB
                    from flask import current_app

                    db_path = current_app.config.get("DATABASE_PATH")
                    db = CentralizedDB(str(db_path)) if db_path else CentralizedDB()
                    if db.is_workspace_owner_user(payload.get("user_id")):
                        payload["is_workspace_owner"] = True
                        request.user = payload
                except Exception:
                    pass
            if not session_is_current(payload):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": {
                                "code": "SESSION_REVOKED",
                                "message": (
                                    "Signed out — this account was opened on "
                                    "another device."
                                ),
                            },
                        }
                    ),
                    401,
                )
            return f(*args, **kwargs)

        return decorated_function


def client_slot(req=None) -> str:
    """'phone' for the NEXORA phone app, 'computer' for a web browser, the Windows exe or a Mac."""
    from flask import request as _r

    req = req or _r
    try:
        body = req.get_json(silent=True) or {}
    except Exception:
        body = {}
    told = str((body.get("client") if isinstance(body, dict) else "") or req.headers.get("X-Nexora-Client") or "").lower()
    if told in ("android", "ios", "phone", "app"):
        return "phone"
    if told in ("web", "desktop", "exe", "mac", "windows", "computer"):
        return "computer"
    ua = (req.headers.get("User-Agent") or "").lower()
    if "mozilla" in ua or "pywebview" in ua or "electron" in ua:
        return "computer"
    return "phone"   # okhttp / Dalvik = the phone app


def _is_founder_payload(payload: dict) -> bool:
    import os

    founder = (os.getenv("WORKSPACE_OWNER_USERNAME") or "kunwar1del").strip().lower() or "kunwar1del"
    return str(payload.get("username") or "").strip().lower() == founder


def session_is_current(payload: dict) -> bool:
    """One phone + one computer per login (owner 21-11); only the founder and logins the founder granted
    "multi device" are free. A token without a session id is refused once the login has a live session."""
    if not isinstance(payload, dict):
        return True
    if _is_founder_payload(payload):
        return True
    token_sid = payload.get("sid")
    try:
        from centralized_db_system.db import CentralizedDB
        from flask import current_app

        db_path = current_app.config.get("DATABASE_PATH")
        db = CentralizedDB(str(db_path)) if db_path else CentralizedDB()
        user_id = payload.get("user_id")
        if db.is_multi_device_allowed(user_id):
            return True
        slots = db.get_slot_sessions(user_id)
        active = None if slots else db.get_active_session(user_id)
    except Exception:
        # Never lock users out because of a DB hiccup.
        return True
    if slots:
        return bool(token_sid) and str(token_sid) in slots.values()
    if not active:
        return True
    return bool(token_sid) and str(active) == str(token_sid)

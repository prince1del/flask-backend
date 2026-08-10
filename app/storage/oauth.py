from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


class GoogleDriveOAuth:
    """Google OAuth 2.0 Flow for Google Drive."""

    CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
    CLIENT_SECRETS_FILE = os.getenv(
        "GOOGLE_OAUTH_CLIENT_SECRETS",
        str(Path(__file__).resolve().parents[2] / "client_secrets.json"),
    )
    SCOPES = ["https://www.googleapis.com/auth/drive"]
    # OAuth round-trip window — forged/expired states must not bind Drive tokens.
    OAUTH_STATE_MAX_AGE_SECONDS = 60 * 60

    @classmethod
    def _state_secret(cls) -> str:
        secret = (os.getenv("SECRET_KEY") or os.getenv("JWT_SECRET_KEY") or "").strip()
        if not secret:
            raise ValueError(
                "SECRET_KEY (or JWT_SECRET_KEY) is required to sign Google OAuth state"
            )
        return secret

    @classmethod
    def _state_serializer(cls) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(
            secret_key=cls._state_secret(),
            salt="nexora-gdrive-oauth-state-v1",
        )

    @classmethod
    def encode_oauth_state(cls, payload: dict[str, Any] | None = None) -> str:
        """Return a signed, timed OAuth state (not forgeable without SECRET_KEY)."""
        data = dict(payload or {})
        return cls._state_serializer().dumps(data)

    @classmethod
    def parse_oauth_state(cls, state: str | None) -> dict[str, Any]:
        """Verify and decode OAuth state. Unsigned/legacy/forged states return {}."""
        if not state:
            return {}
        try:
            data = cls._state_serializer().loads(
                state, max_age=cls.OAUTH_STATE_MAX_AGE_SECONDS
            )
        except (BadSignature, SignatureExpired, ValueError, TypeError):
            return {}
        except Exception:
            return {}

        if not isinstance(data, dict):
            return {}
        # Normalize aliases / types from older clients
        if "workspace_id" not in data and data.get("work_id") is not None:
            data["workspace_id"] = data.get("work_id")
        if data.get("workspace_id") is not None:
            data["workspace_id"] = str(data["workspace_id"])
        if data.get("user_id") is not None:
            try:
                data["user_id"] = int(data["user_id"])
            except (TypeError, ValueError):
                pass
        return data

    @classmethod
    def _resolve_redirect_uri(
        cls,
        host_url: str | None = None,
        redirect_uri: str | None = None,
    ) -> str:
        if redirect_uri:
            return redirect_uri.rstrip("/")
        # Exact match with Google Cloud Console Authorized redirect URIs is required.
        if cls.REDIRECT_URI:
            return cls.REDIRECT_URI.strip()
        if host_url:
            return host_url.rstrip("/") + "/api/v1/storage/oauth-callback"
        raise ValueError(
            "Google Drive OAuth redirect URI is not configured. "
            "Set GOOGLE_OAUTH_REDIRECT_URI to a URI registered in Google Cloud Console "
            "(e.g. https://YOUR-HOST/api/v1/storage/oauth-callback)."
        )

    @classmethod
    def _load_client_secrets_file(cls) -> dict[str, Any] | None:
        if not cls.CLIENT_SECRETS_FILE:
            return None
        secrets_path = Path(cls.CLIENT_SECRETS_FILE)
        if not secrets_path.exists():
            return None
        try:
            return json.loads(secrets_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    @classmethod
    def _get_client_config(cls, redirect_uri: str) -> dict[str, Any]:
        client_config = cls._load_client_secrets_file()
        if client_config is not None:
            if redirect_uri:
                if "web" in client_config:
                    client_config["web"]["redirect_uris"] = [redirect_uri]
                elif "installed" in client_config:
                    client_config["installed"]["redirect_uris"] = [redirect_uri]
            return client_config

        missing = [
            name
            for name, value in {
                "GOOGLE_OAUTH_CLIENT_ID": cls.CLIENT_ID,
                "GOOGLE_OAUTH_CLIENT_SECRET": cls.CLIENT_SECRET,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(
                "Google Drive OAuth is not configured. Set the following environment variables: "
                + ", ".join(missing)
                + ", or provide a valid client_secrets.json file."
            )

        return {
            "web": {
                "client_id": cls.CLIENT_ID,
                "client_secret": cls.CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        }

    @classmethod
    def get_auth_url(
        cls,
        host_url: str | None = None,
        redirect_uri: str | None = None,
        state_payload: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Generate Google OAuth authorization URL."""
        try:
            from google_auth_oauthlib.flow import Flow
        except ImportError as exc:
            raise RuntimeError(
                "google-auth-oauthlib is required for Google OAuth flow"
            ) from exc

        resolved_redirect = cls._resolve_redirect_uri(host_url=host_url, redirect_uri=redirect_uri)
        client_config = cls._get_client_config(resolved_redirect)
        flow = Flow.from_client_config(client_config, scopes=cls.SCOPES)
        flow.redirect_uri = resolved_redirect
        state = cls.encode_oauth_state(state_payload)
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=state,
        )
        return auth_url, state

    @classmethod
    def exchange_code_for_token(
        cls,
        auth_code: str,
        host_url: str | None = None,
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """Exchange authorization code for access token."""
        try:
            from google_auth_oauthlib.flow import Flow
        except ImportError as exc:
            raise RuntimeError(
                "google-auth-oauthlib is required for Google OAuth flow"
            ) from exc

        resolved_redirect = cls._resolve_redirect_uri(host_url=host_url, redirect_uri=redirect_uri)
        client_config = cls._get_client_config(resolved_redirect)
        flow = Flow.from_client_config(client_config, scopes=cls.SCOPES)
        flow.redirect_uri = resolved_redirect
        flow.fetch_token(code=auth_code)
        credentials = flow.credentials
        return json.loads(credentials.to_json())

    @staticmethod
    def refresh_token(refresh_token: str) -> dict[str, Any]:
        """Refresh expired access token."""
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
        except ImportError as exc:
            raise RuntimeError(
                "google-auth and google-auth-transport-requests are required for token refresh"
            ) from exc

        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        credentials.refresh(Request())
        return json.loads(credentials.to_json())

    @staticmethod
    def revoke_token(access_token: str) -> dict[str, Any]:
        """Revoke access token (disconnect Drive)."""
        # Basic revoke implementation using HTTP request.
        from urllib.request import Request, urlopen

        revoke_url = "https://oauth2.googleapis.com/revoke"
        request = Request(revoke_url + f"?token={access_token}")
        with urlopen(request) as response:
            return {"status": response.status, "reason": response.reason}

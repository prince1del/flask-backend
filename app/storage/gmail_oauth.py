from __future__ import annotations

from typing import Any

from app.storage.oauth import GoogleDriveOAuth, _load_dotenv_once


class GmailOAuth(GoogleDriveOAuth):
    """Google OAuth 2.0 Flow for Gmail read-only access (CI/SO auto-import).

    Reuses the same Google Cloud OAuth client (CLIENT_ID/SECRET) as Drive —
    only the requested scope and redirect URI differ, so this is a separate
    token grant, not a change to the existing Drive integration.
    """

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    @classmethod
    def _resolve_redirect_uri(
        cls,
        host_url: str | None = None,
        redirect_uri: str | None = None,
    ) -> str:
        if redirect_uri:
            return redirect_uri.rstrip("/")
        env_redirect = cls._env("GOOGLE_GMAIL_OAUTH_REDIRECT_URI")
        host = (host_url or "").rstrip("/")
        host_is_public = bool(host) and "localhost" not in host and "127.0.0.1" not in host
        env_is_local = (
            not env_redirect
            or "localhost" in env_redirect
            or "127.0.0.1" in env_redirect
        )
        if env_redirect and not (host_is_public and env_is_local):
            return env_redirect.rstrip("/")
        if host:
            return host + "/api/v1/mail-sync/oauth-callback"
        raise ValueError(
            "Gmail OAuth redirect URI is not configured. Set "
            "GOOGLE_GMAIL_OAUTH_REDIRECT_URI to a URI registered in Google Cloud "
            "Console (e.g. https://YOUR-HOST/api/v1/mail-sync/oauth-callback)."
        )

    @classmethod
    def _state_serializer(cls):
        from itsdangerous import URLSafeTimedSerializer

        return URLSafeTimedSerializer(
            secret_key=cls._state_secret(),
            salt="nexora-gmail-oauth-state-v1",
        )

    @staticmethod
    def refresh_token(refresh_token: str) -> dict[str, Any]:
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
            client_id=GmailOAuth._client_id(),
            client_secret=GmailOAuth._client_secret(),
            scopes=GmailOAuth.SCOPES,
        )
        credentials.refresh(Request())
        return __import__("json").loads(credentials.to_json())

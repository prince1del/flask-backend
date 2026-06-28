import json
import os
from typing import Any


class GoogleDriveOAuth:
    """Google OAuth 2.0 Flow for Google Drive."""

    CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
    SCOPES = ["https://www.googleapis.com/auth/drive"]

    @classmethod
    def _get_client_config(cls) -> dict[str, Any]:
        return {
            "web": {
                "client_id": cls.CLIENT_ID,
                "client_secret": cls.CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [cls.REDIRECT_URI],
            }
        }

    @classmethod
    def get_auth_url(cls) -> tuple[str, str]:
        """Generate Google OAuth authorization URL."""
        try:
            from google_auth_oauthlib.flow import Flow
        except ImportError as exc:
            raise RuntimeError(
                "google-auth-oauthlib is required for Google OAuth flow"
            ) from exc

        flow = Flow.from_client_config(cls._get_client_config(), scopes=cls.SCOPES)
        flow.redirect_uri = cls.REDIRECT_URI
        auth_url, state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true"
        )
        return auth_url, state

    @classmethod
    def exchange_code_for_token(cls, auth_code: str) -> dict[str, Any]:
        """Exchange authorization code for access token."""
        try:
            from google_auth_oauthlib.flow import Flow
        except ImportError as exc:
            raise RuntimeError(
                "google-auth-oauthlib is required for Google OAuth flow"
            ) from exc

        flow = Flow.from_client_config(cls._get_client_config(), scopes=cls.SCOPES)
        flow.redirect_uri = cls.REDIRECT_URI
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

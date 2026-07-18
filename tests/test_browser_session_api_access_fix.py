"""
Verifies the fix for a critical bug found via REAL browser testing
(4 July 2026, Bombay Dyeing GT North test): the frontend's own
JavaScript (app.js) makes fetch() calls to /api/v1/... endpoints using
the browser's session cookie (from the HTML /login form) — but
require_jwt_auth's ORIGINAL check order ALWAYS demanded a genuine JWT
Bearer token for any path starting with "/api/", regardless of whether
a valid session existed. Nothing in the browser login flow ever
issues/stores a JWT for app.js to attach, so EVERY such fetch() call
failed with 401 — breaking entire dashboard sections (e.g. the
"Customers" tab silently doing nothing) for every real browser user,
while all JWT-based automated tests kept passing throughout this
project because they never exercised this exact code path.
"""
import importlib

from centralized_db_system.db import CentralizedDB


def setup_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "session_api_access_test.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "session-api-access-test-key")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply_env()

    app = web_app_module.create_app()
    app.config["TESTING"] = True

    db = CentralizedDB(str(db_path))
    db.create_user(
        "session_api_user", "SessionApiPass123!",
        role="sales_executive", workspace_id="ws-session-api",
    )

    return app.test_client()


def test_browser_session_can_call_api_endpoints_without_a_jwt_token(tmp_path, monkeypatch):
    """
    THE ACTUAL BUG: log in via the real HTML <form> (session-based,
    exactly like a human using the browser), then call a JSON /api/
    endpoint the SAME WAY app.js does — via a plain fetch()-equivalent
    request, with NO Authorization header at all, relying only on the
    session cookie the browser already carries.

    Before the fix: this always returned 401, no matter how correctly
    the user was logged in, because /api/ paths were unconditionally
    routed through the JWT-only check.
    """
    client = setup_auth_app(tmp_path, monkeypatch)

    login_resp = client.post(
        "/login",
        data={"username": "session_api_user", "password": "SessionApiPass123!"},
    )
    assert login_resp.status_code == 302, "Browser login itself should succeed"

    # No Authorization header, no JSON body — exactly what a plain
    # fetch('/api/v1/target-achievement/years') from app.js sends,
    # relying purely on the session cookie the client already carries.
    api_resp = client.get("/api/v1/target-achievement/years")
    assert api_resp.status_code == 200, (
        f"BUG REPRODUCED: a browser-session-logged-in user got rejected "
        f"from an /api/ endpoint. Status: {api_resp.status_code}. "
        f"Body: {api_resp.get_data(as_text=True)[:300]}"
    )


def test_api_request_without_any_session_or_token_still_requires_auth(tmp_path, monkeypatch):
    """Sanity check: the fix must not accidentally make /api/ routes
    open to everyone — a request with NO session and NO Authorization
    header must still be rejected."""
    client = setup_auth_app(tmp_path, monkeypatch)

    resp = client.get("/api/v1/target-achievement/years")
    assert resp.status_code == 401


def test_explicit_bearer_token_still_works_independent_of_session(tmp_path, monkeypatch):
    """Sanity check: genuine JWT/Bearer-token callers (e.g. a mobile
    app, or our own automated tests) must continue to work exactly as
    before — this fix must not change that path at all."""
    client = setup_auth_app(tmp_path, monkeypatch)

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": "session_api_user", "password": "SessionApiPass123!"},
    )
    assert login_resp.status_code == 200
    token = login_resp.get_json()["data"]["access_token"]

    resp = client.get(
        "/api/v1/target-achievement/years",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

"""
Verifies the session-based (browser form) login flow correctly
populates request.user — reproducing the EXACT bug found during
real-world browser testing (4 July 2026, Bombay Dyeing GT North test
user).

ROOT CAUSE: Every automated test written so far logged in via the
JSON API (POST /api/v1/auth/login -> Bearer token), which correctly
sets request.user via JWTService.require_auth(). But a REAL human
using the actual login page (GET/POST /login, an HTML form) only ever
got session["authenticated"] = True and session["username"] set —
role and workspace_id were never stored, and request.user was NEVER
populated for that path in require_jwt_auth's session branch.

Result: any route relying on get_workspace_id() (e.g. /analytics)
crashed with "RuntimeError: Workspace ID cannot be determined..." for
every real browser user, while every automated JWT-based test kept
passing — completely hiding this bug from all of our prior testing.
"""
import importlib

from centralized_db_system.db import CentralizedDB


def setup_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "session_login_test.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "session-login-test-key")

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
        "browser_test_user", "BrowserPass123!",
        role="sales_executive", workspace_id="ws-browser-test",
    )

    return app.test_client()


def test_browser_form_login_can_reach_workspace_scoped_route(tmp_path, monkeypatch):
    """
    THE ACTUAL BUG: log in through the real HTML <form> (not the JSON
    API), the way an actual human using a browser does, then hit a
    route that depends on get_workspace_id() (like /analytics).

    Before the fix: this crashed with a 500 RuntimeError for every
    single browser user, no matter how correctly their workspace_id
    was set up in the database.
    """
    client = setup_auth_app(tmp_path, monkeypatch)

    # This is a real form POST (application/x-www-form-urlencoded),
    # exactly matching what a browser <form method="post"> sends —
    # deliberately NOT using json=... here, since that's what
    # distinguishes the browser path from the JWT/API path.
    login_resp = client.post(
        "/login",
        data={"username": "browser_test_user", "password": "BrowserPass123!"},
        follow_redirects=False,
    )
    assert login_resp.status_code == 302, (
        f"Expected a redirect after successful browser login, got "
        f"{login_resp.status_code}: {login_resp.get_data(as_text=True)}"
    )

    # Now, using the SAME client (which carries the session cookie
    # from the login above — no Authorization header, no JSON), hit a
    # route that calls get_workspace_id() internally.
    analytics_resp = client.get("/analytics")
    assert analytics_resp.status_code == 200, (
        f"BUG REPRODUCED: browser-session-logged-in user got a crash "
        f"instead of a working page. Status: {analytics_resp.status_code}. "
        f"Body: {analytics_resp.get_data(as_text=True)[:500]}"
    )


def test_browser_login_session_carries_correct_workspace(tmp_path, monkeypatch):
    """A second workspace's browser-logged-in user must not see the
    first workspace's data — proving the session now carries the
    CORRECT workspace_id, not just "a" workspace_id."""
    client = setup_auth_app(tmp_path, monkeypatch)
    db_path = tmp_path / "session_login_test.sqlite3"
    db = CentralizedDB(str(db_path))

    db.create_user(
        "browser_user_ws2", "BrowserPass456!",
        role="sales_executive", workspace_id="ws-browser-two",
    )
    db.add_master_distributor(
        name="WS Browser Test Distributor", firm_name="WS1 Firm",
        gst_no="GST-WSB-1", workspace_id="ws-browser-test",
    )
    db.add_master_distributor(
        name="WS2 Browser Distributor", firm_name="WS2 Firm",
        gst_no="GST-WSB-2", workspace_id="ws-browser-two",
    )

    client.post(
        "/login",
        data={"username": "browser_user_ws2", "password": "BrowserPass456!"},
    )

    resp = client.get("/analytics")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "1 distributors active" in body, (
        "ws-browser-two's session should see its own 1 distributor"
    )

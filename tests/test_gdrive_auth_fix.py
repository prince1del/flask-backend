"""
Corrected test for gdrive.py's _get_current_user() fallback fix.

WHY THIS TEST IS DIFFERENT FROM PREVIOUS ATTEMPTS:

Attempt 1 hit /api/gdrive/connect/1 with AUTH_ENABLED=true and no
Authorization header. That gets rejected by @require_jwt_auth itself
(NO_TOKEN, 401) before the view function's body — including our
try/except fix — ever runs. It proves the decorator works, which we
already knew; it does NOT prove the fallback inside the view was fixed.

Attempt 2 called _get_current_user() directly in a bare
app.test_request_context(). That's trivially guaranteed to raise
(request.user is never set in a bare context), so it can't fail
regardless of whether the real fix exists.

THIS test creates the one condition where the OLD bug could actually
fire: AUTH_ENABLED=false. In that mode, require_jwt_auth's decorator
skips the JWT check entirely and calls the view function directly —
meaning request.user is genuinely never set, and execution reaches
the try/except we added inside the view itself. This is the only
condition that exercises the real code path end-to-end over a real
HTTP request.
"""
import importlib


def setup_noauth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "gdrive_noauth_test.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "false")
        monkeypatch.setenv("SECRET_KEY", "gdrive-fix-test-key")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    # IMPORTANT: reloading web_app_module re-executes its top-level
    # load_env_file() call, which reads the real .env file on disk and
    # OVERWRITES our monkeypatched env vars (including AUTH_ENABLED).
    # Every test using this pattern with AUTH_ENABLED="false" was
    # silently getting the real .env's AUTH_ENABLED=true instead,
    # unless we re-apply our overrides AFTER the reload.
    importlib.reload(web_app_module)
    _apply_env()

    app = web_app_module.create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_gdrive_connect_returns_clean_401_when_user_context_missing(tmp_path, monkeypatch):
    """
    Real HTTP request, AUTH_ENABLED=false, no Authorization header.

    require_jwt_auth will skip its own check (because auth is disabled)
    and call start_gdrive_connect() directly. Inside the view,
    request.user was never set, so _get_current_user() must raise
    RuntimeError, and the view must catch it and return a clean 401 —
    NOT silently proceed with user_id=1, and NOT crash with an
    uncaught exception.
    """
    client = setup_noauth_app(tmp_path, monkeypatch)

    resp = client.get("/api/gdrive/connect/1")

    # Must not succeed
    assert resp.status_code == 401, (
        f"Expected 401, got {resp.status_code}. "
        f"Body: {resp.get_data(as_text=True)}"
    )

    body = resp.get_json()
    assert body is not None, "Expected a JSON error body, got none"
    assert body.get("success") is False
    assert "Authentication required" in body.get("error", ""), (
        f"Expected 'Authentication required' in error message, got: {body}"
    )


def test_gdrive_status_returns_clean_401_when_user_context_missing(tmp_path, monkeypatch):
    """Same condition, applied to the /status/<user_id> endpoint."""
    client = setup_noauth_app(tmp_path, monkeypatch)

    resp = client.get("/api/gdrive/status/1")

    assert resp.status_code == 401, (
        f"Expected 401, got {resp.status_code}. "
        f"Body: {resp.get_data(as_text=True)}"
    )
    body = resp.get_json()
    assert body is not None
    assert body.get("success") is False
    assert "Authentication required" in body.get("error", "")


def test_gdrive_disconnect_returns_clean_401_when_user_context_missing(tmp_path, monkeypatch):
    """Same condition, applied to the /disconnect/<user_id> endpoint."""
    client = setup_noauth_app(tmp_path, monkeypatch)

    resp = client.post("/api/gdrive/disconnect/1")

    assert resp.status_code == 401, (
        f"Expected 401, got {resp.status_code}. "
        f"Body: {resp.get_data(as_text=True)}"
    )
    body = resp.get_json()
    assert body is not None
    assert body.get("success") is False
    assert "Authentication required" in body.get("error", "")

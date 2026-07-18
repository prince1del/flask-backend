"""
Verifies the password-hashing compatibility fix between the two
user-creation paths that write to the same `users` table:

  1. POST /api/v1/admin/users (admin.py) — uses SQLAlchemy's
     app.models.User.set_password()
  2. CentralizedDB.create_user() (db.py) — used by
     ensure_default_admin_user() at app boot, uses Werkzeug's
     generate_password_hash() directly

Login (POST /api/v1/auth/login) always goes through
CentralizedDB.authenticate_user(), which uses Werkzeug's
check_password_hash().

BEFORE this fix: User.set_password() used raw, unsalted SHA-256.
Any user created via the admin API route would get a hash format
check_password_hash() could never verify — meaning EVERY admin-created
user was silently, permanently unable to log in (no clear error,
just "Invalid username or password" on every attempt).

This test creates a user through the REAL admin API route (not by
calling CentralizedDB.create_user() directly) and then attempts a
REAL login with that exact user — proving the full chain works
end-to-end, not just that each half works in isolation.
"""
import importlib

from centralized_db_system.db import CentralizedDB


def setup_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "password_hash_fix_test.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "password-hash-fix-test-key")
        monkeypatch.setenv("ADMIN_USERNAME", "admin")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply_env()  # reload() re-runs load_env_file(), which clobbers these — reapply after

    app = web_app_module.create_app()
    app.config["TESTING"] = True

    # The founder/admin account, created the "old" way (CentralizedDB
    # directly), since that's how ensure_default_admin_user() works at
    # real app boot. This account will create the NEW user via the API.
    db = CentralizedDB(str(db_path))
    db.create_user("admin", "AdminPass123!", role="admin", workspace_id="default")

    return app.test_client()


def login(client, username: str, password: str):
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )


def test_user_created_via_admin_api_can_actually_log_in(tmp_path, monkeypatch):
    client = setup_auth_app(tmp_path, monkeypatch)

    # Step 1: founder/admin logs in (created the CentralizedDB way —
    # this path was already known to work, confirming our baseline).
    admin_login = login(client, "admin", "AdminPass123!")
    assert admin_login.status_code == 200, (
        f"Baseline admin login failed — something else is broken: "
        f"{admin_login.get_data(as_text=True)}"
    )
    admin_token = admin_login.get_json()["data"]["access_token"]

    # Step 2: admin creates a NEW user via the REST API route
    # (app/routes/admin.py -> SQLAlchemy User.set_password()) — this is
    # the path that was broken before this fix.
    create_resp = client.post(
        "/api/v1/admin/users",
        json={
            "username": "new_sales_exec",
            "email": "sales_exec@example.com",
            "password": "SalesExecPass123!",
            "role": "sales_executive",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_resp.status_code == 201, (
        f"User creation via admin API failed: {create_resp.get_data(as_text=True)}"
    )

    # Step 3: THE ACTUAL BUG — attempt to log in as the newly created
    # user. Before the fix, this always failed with 401
    # INVALID_CREDENTIALS, even with the exact correct password, because
    # the stored hash format was incompatible with the login check.
    new_user_login = login(client, "new_sales_exec", "SalesExecPass123!")
    assert new_user_login.status_code == 200, (
        f"BUG REPRODUCED: user created via admin API cannot log in with "
        f"their correct password. Response: {new_user_login.get_data(as_text=True)}"
    )
    body = new_user_login.get_json()
    assert body["data"]["user"]["username"] == "new_sales_exec"
    assert body["data"]["user"]["role"] == "sales_executive"


def test_wrong_password_is_still_correctly_rejected(tmp_path, monkeypatch):
    """Sanity check: the fix shouldn't make the check_password() always
    return True — a genuinely wrong password must still fail."""
    client = setup_auth_app(tmp_path, monkeypatch)

    admin_login = login(client, "admin", "AdminPass123!")
    admin_token = admin_login.get_json()["data"]["access_token"]

    client.post(
        "/api/v1/admin/users",
        json={
            "username": "another_user",
            "email": "another@example.com",
            "password": "CorrectPass123!",
            "role": "sales_executive",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    wrong_login = login(client, "another_user", "WrongPassword999!")
    assert wrong_login.status_code == 401

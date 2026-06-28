import os

from app.web_app import create_app
from centralized_db_system.db import CentralizedDB


def test_auth_helpers_support_registration_and_login(tmp_path):
    db = CentralizedDB(str(tmp_path / "cloud-auth.sqlite3"))

    created = db.create_user("admin", "StrongPass123")
    assert created["username"] == "admin"
    assert db.authenticate_user("admin", "StrongPass123") is True
    assert db.authenticate_user("admin", "wrong") is False


def test_web_app_redirects_to_login_when_auth_is_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("CLOUD_DATABASE_URL", f"sqlite:///{tmp_path / 'cloud-web.sqlite3'}")

    app = create_app()
    client = app.test_client()

    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_jwt_login_allows_access_to_protected_api(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("CLOUD_DATABASE_URL", f"sqlite:///{tmp_path / 'jwt-auth.sqlite3'}")

    db = CentralizedDB(str(tmp_path / "jwt-auth.sqlite3"))
    db.create_user("admin", "StrongPass123")

    app = create_app()
    client = app.test_client()

    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "StrongPass123"},
    )
    assert login_response.status_code == 200
    payload = login_response.get_json()
    assert payload["data"]["access_token"]

    protected_response = client.get(
        "/api/v1/workspaces",
        headers={"Authorization": f"Bearer {payload['data']['access_token']}"},
    )
    assert protected_response.status_code == 200

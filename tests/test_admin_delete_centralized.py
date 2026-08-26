"""CentralizedDB admin delete + secret-question password reset."""

from __future__ import annotations

import os

import pytest

from app.web_app import create_app
from centralized_db_system.db import CentralizedDB


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "cdb_admin_delete.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-for-admin-delete")
    monkeypatch.setenv("WORKSPACE_OWNER_USERNAME", "kunwar1del")

    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def cdb(app):
    return CentralizedDB(str(app.config["DATABASE_PATH"]))


def _bootstrap_founder(cdb: CentralizedDB) -> dict:
    created = cdb.create_user(
        "kunwar1del",
        "FounderPass1!",
        role="sales_executive",
        workspace_id="bombay_dyeing_gt_north",
        email="founder@example.com",
    )
    cdb.set_workspace_owner(int(created["id"]), True)
    return created


def _login(client, username: str, password: str) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.get_json()
    token = resp.get_json()["data"]["access_token"]
    return token


def test_admin_delete_uses_centralized_db(client, cdb):
    _bootstrap_founder(cdb)
    victim = cdb.create_user(
        "e2e_iso_test1",
        "VictimPass1!",
        role="sales_executive",
        workspace_id="test_tenant_iso",
        email="iso@example.com",
    )
    token = _login(client, "kunwar1del", "FounderPass1!")

    resp = client.delete(
        f"/api/v1/admin/users/{victim['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["success"] is True
    assert cdb.get_user_profile(int(victim["id"])) is None


def test_admin_delete_refuses_hop_admin_house_of_prizm(client, cdb):
    _bootstrap_founder(cdb)
    hop = cdb.create_user(
        "hop_prod_admin",
        "HopPass1!",
        role="hop_admin",
        workspace_id="house_of_prizm",
        email="hop_prod_admin@example.com",
    )
    token = _login(client, "kunwar1del", "FounderPass1!")
    resp = client.delete(
        f"/api/v1/admin/users/{hop['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert cdb.get_user_profile(int(hop["id"])) is not None


def test_self_delete_with_password(client, cdb):
    _bootstrap_founder(cdb)
    user = cdb.create_user(
        "temp_self_del",
        "SelfPass1!",
        role="sales_executive",
        workspace_id="temp_self_ws",
        email="self@example.com",
    )
    token = _login(client, "temp_self_del", "SelfPass1!")
    bad = client.post(
        "/api/v1/me/delete-account",
        headers={"Authorization": f"Bearer {token}"},
        json={"password": "wrong"},
    )
    assert bad.status_code == 401
    ok = client.post(
        "/api/v1/me/delete-account",
        headers={"Authorization": f"Bearer {token}"},
        json={"password": "SelfPass1!"},
    )
    assert ok.status_code == 200, ok.get_json()
    assert cdb.get_user_profile(int(user["id"])) is None


def test_forgot_password_with_secret_question(client, cdb):
    user = cdb.create_user(
        "secret_user",
        "OldPass12!",
        role="sales_executive",
        workspace_id="secret_ws",
        email="secret@example.com",
    )
    cdb.set_secret_question(
        int(user["id"]),
        CentralizedDB.SECRET_QUESTIONS[0],
        "Chandigarh",
    )
    hint = client.post(
        "/api/v1/auth/secret-question-hint",
        json={"username": "secret_user"},
    )
    assert hint.status_code == 200
    assert hint.get_json()["data"]["secret_question"] == CentralizedDB.SECRET_QUESTIONS[0]

    bad = client.post(
        "/api/v1/auth/forgot-password",
        json={
            "username": "secret_user",
            "secret_answer": "wrong city",
            "new_password": "NewPass12!",
        },
    )
    assert bad.status_code == 400

    ok = client.post(
        "/api/v1/auth/forgot-password",
        json={
            "username": "secret_user",
            "secret_answer": "chandigarh",
            "new_password": "NewPass12!",
        },
    )
    assert ok.status_code == 200, ok.get_json()
    assert cdb.authenticate_user("secret_user", "NewPass12!")


def test_signup_requires_email_and_secret(client, cdb):
    missing = client.post(
        "/api/v1/signup",
        json={
            "company_name": "Acme Co",
            "username": "acme_admin",
            "password": "AcmePass1!",
            "category": "service",
        },
    )
    assert missing.status_code == 400

    ok = client.post(
        "/api/v1/signup",
        json={
            "company_name": "Acme Co",
            "username": "acme_admin",
            "password": "AcmePass1!",
            "email": "acme@example.com",
            "category": "service",
            "secret_question": CentralizedDB.SECRET_QUESTIONS[1],
            "secret_answer": "Sharma",
        },
    )
    assert ok.status_code == 201, ok.get_json()
    profile = cdb.get_user_profile(int(ok.get_json()["data"]["user_id"]))
    assert profile["has_secret_question"] is True
    assert profile["email"] == "acme@example.com"

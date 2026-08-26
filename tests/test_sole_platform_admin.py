"""Sole platform admin = WORKSPACE_OWNER_USERNAME (kunwar1del)."""

from __future__ import annotations

import sqlite3

import pytest
from app import create_app
from centralized_db_system.db import CentralizedDB


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "sole_admin.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "test-secret-sole-admin-key32")
    monkeypatch.setenv("WORKSPACE_OWNER_USERNAME", "kunwar1del")
    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application


def test_promote_strips_all_other_owners_and_literal_admins(app):
    db = CentralizedDB(app.config["DATABASE_PATH"])
    db.create_user("kunwar1del", "pass1234", role="sales_executive", workspace_id="ws_bd")
    other = db.create_user(
        "legacy_admin", "pass1234", role="admin", workspace_id="ws_other"
    )
    hop = db.create_user(
        "hop_shell_user", "pass1234", role="hop_admin", workspace_id="house_of_prizm"
    )
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        conn.execute(
            "UPDATE users SET is_workspace_owner = 1 WHERE id = ?",
            (int(other["id"]),),
        )
        conn.commit()

    result = db.promote_workspace_owner("kunwar1del", takeover_workspace_data=False)
    assert result["action"] == "promoted"
    assert result["stripped_other_owners"] >= 1
    assert result["stripped_literal_admins"] >= 1

    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        conn.row_factory = sqlite3.Row
        rows = {
            r["username"]: dict(r)
            for r in conn.execute(
                "SELECT id, username, role, is_workspace_owner FROM users"
            )
        }
    assert int(rows["kunwar1del"]["is_workspace_owner"] or 0) == 1
    assert int(rows["legacy_admin"]["is_workspace_owner"] or 0) == 0
    assert rows["legacy_admin"]["role"] == "sales_executive"
    assert rows["hop_shell_user"]["role"] == "hop_admin"  # Business shell kept


def test_admin_api_rejects_creating_role_admin(app):
    from app.jwt_service import JWTService

    db = CentralizedDB(app.config["DATABASE_PATH"])
    owner = db.create_user(
        "kunwar1del", "pass1234", role="sales_executive", workspace_id="ws_bd"
    )
    db.promote_workspace_owner("kunwar1del", takeover_workspace_data=False)
    service = JWTService(secret_key=app.config["SECRET_KEY"])
    token, _ = service.create_tokens(
        user_id=int(owner["id"]),
        username="kunwar1del",
        role="sales_executive",
        workspace_id="ws_bd",
        is_workspace_owner=True,
    )
    client = app.test_client()
    resp = client.post(
        "/api/v1/admin/users",
        json={
            "username": "newadmin",
            "email": "newadmin@example.com",
            "password": "pass1234",
            "role": "admin",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "Invalid role" in (resp.get_json().get("message") or "")

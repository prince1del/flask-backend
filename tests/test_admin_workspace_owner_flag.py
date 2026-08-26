"""Hardening: only WORKSPACE_OWNER_USERNAME may hold / receive supreme owner.

- API cannot grant is_workspace_owner=True
- API can demote others, not the supreme username
- Workspace role APIs cannot assign role=admin
"""

from __future__ import annotations

import pytest
from app import create_app
from app.jwt_service import JWTService
from centralized_db_system.db import CentralizedDB


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "owner_harden.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "test-secret-owner-harden-32b")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("WORKSPACE_OWNER_USERNAME", "kunwar1del")

    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


def _cdb(app) -> CentralizedDB:
    return CentralizedDB(app.config["DATABASE_PATH"])


def _token(app, *, user_id, username, role, workspace_id, is_workspace_owner=False):
    service = JWTService(secret_key=app.config["SECRET_KEY"])
    access, _ = service.create_tokens(
        user_id=user_id,
        username=username,
        role=role,
        workspace_id=workspace_id,
        is_workspace_owner=is_workspace_owner,
    )
    return access


def test_api_cannot_grant_owner_flag_to_other_user(client, app):
    db = _cdb(app)
    owner = db.create_user(
        "kunwar1del", "pass1234", role="sales_executive", workspace_id="ws_bd"
    )
    target = db.create_user(
        "kunwar.julka", "pass1234", role="sales_executive", workspace_id="ws_other"
    )
    db.promote_workspace_owner("kunwar1del", takeover_workspace_data=False)
    assert db.is_workspace_owner_user(int(target["id"])) is False

    token = _token(
        app,
        user_id=int(owner["id"]),
        username="kunwar1del",
        role="sales_executive",
        workspace_id="ws_bd",
        is_workspace_owner=True,
    )
    resp = client.put(
        f"/api/v1/admin/users/{int(target['id'])}",
        json={"is_workspace_owner": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert db.is_workspace_owner_user(int(target["id"])) is False


def test_api_can_demote_non_supreme_owner(client, app):
    db = _cdb(app)
    owner = db.create_user(
        "kunwar1del", "pass1234", role="sales_executive", workspace_id="ws_bd"
    )
    target = db.create_user(
        "test_tenant_qa_user2", "pass1234", role="sales_executive", workspace_id="ws_qa"
    )
    # Bypass setter restriction by writing via promote-scope then clear peers —
    # simulate a legacy bad row: force flag with raw SQL after create.
    import sqlite3

    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        conn.execute(
            "UPDATE users SET is_workspace_owner = 1 WHERE id = ?",
            (int(target["id"]),),
        )
        conn.commit()
    db.promote_workspace_owner("kunwar1del", takeover_workspace_data=False)

    # Re-plant leak flag on target (promote clears same-workspace peers only)
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        conn.execute(
            "UPDATE users SET is_workspace_owner = 1 WHERE id = ?",
            (int(target["id"]),),
        )
        conn.commit()
    assert db.is_workspace_owner_user(int(target["id"])) is True

    token = _token(
        app,
        user_id=int(owner["id"]),
        username="kunwar1del",
        role="sales_executive",
        workspace_id="ws_bd",
        is_workspace_owner=True,
    )
    resp = client.put(
        f"/api/v1/admin/users/{int(target['id'])}",
        json={"is_workspace_owner": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["data"]["is_workspace_owner"] is False
    assert db.is_workspace_owner_user(int(target["id"])) is False


def test_api_cannot_demote_supreme_owner(client, app):
    db = _cdb(app)
    owner = db.create_user(
        "kunwar1del", "pass1234", role="sales_executive", workspace_id="ws_bd"
    )
    db.promote_workspace_owner("kunwar1del", takeover_workspace_data=False)

    token = _token(
        app,
        user_id=int(owner["id"]),
        username="kunwar1del",
        role="sales_executive",
        workspace_id="ws_bd",
        is_workspace_owner=True,
    )
    resp = client.put(
        f"/api/v1/admin/users/{int(owner['id'])}",
        json={"is_workspace_owner": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert db.is_workspace_owner_user(int(owner["id"])) is True


def test_workspace_role_api_rejects_admin(client, app):
    db = _cdb(app)
    owner = db.create_user(
        "kunwar1del", "pass1234", role="sales_executive", workspace_id="ws_bd"
    )
    target = db.create_user(
        "kunwar.julka", "pass1234", role="sales_executive", workspace_id="ws_other"
    )
    db.promote_workspace_owner("kunwar1del", takeover_workspace_data=False)

    token = _token(
        app,
        user_id=int(owner["id"]),
        username="kunwar1del",
        role="sales_executive",
        workspace_id="ws_bd",
        is_workspace_owner=True,
    )
    resp = client.put(
        f"/api/v1/workspace/users/{int(target['id'])}/role",
        json={"role": "admin"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    profile = db.get_user_profile(int(target["id"]))
    assert profile["role"] == "sales_executive"


def test_db_set_workspace_owner_true_rejects_non_supreme(app):
    db = _cdb(app)
    target = db.create_user(
        "someone_else", "pass1234", role="sales_executive", workspace_id="ws_x"
    )
    with pytest.raises(ValueError, match="only allowed for kunwar1del"):
        db.set_workspace_owner(int(target["id"]), True)

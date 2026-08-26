"""PUT /api/v1/admin/users/<id> is_workspace_owner via CentralizedDB.

Production auth users live in DATABASE_PATH sqlite, not the empty
SQLAlchemy users table. Workspace owners (sales_executive + flag) must
be able to clear the flag without a SQLAlchemy row existing.
"""

from __future__ import annotations

import pytest
from app import create_app
from app.jwt_service import JWTService
from centralized_db_system.db import CentralizedDB


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "owner_flag.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "test-secret-owner-flag")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")

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


def test_workspace_owner_can_clear_flag_without_sqlalchemy_row(client, app):
    db = _cdb(app)
    owner = db.create_user(
        "kunwar1del",
        "pass1234",
        role="sales_executive",
        workspace_id="bombay_dyeing_gt_north",
    )
    target = db.create_user(
        "kunwar.julka",
        "pass1234",
        role="sales_executive",
        workspace_id="exec_kunwar_julka",
    )
    db.set_workspace_owner(int(owner["id"]), True)
    db.set_workspace_owner(int(target["id"]), True)
    assert db.is_workspace_owner_user(int(target["id"])) is True

    token = _token(
        app,
        user_id=int(owner["id"]),
        username="kunwar1del",
        role="sales_executive",
        workspace_id="bombay_dyeing_gt_north",
        is_workspace_owner=True,
    )
    resp = client.put(
        f"/api/v1/admin/users/{int(target['id'])}",
        json={"is_workspace_owner": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["username"] == "kunwar.julka"
    assert body["data"]["is_workspace_owner"] is False
    assert db.is_workspace_owner_user(int(target["id"])) is False


def test_plain_executive_cannot_toggle_owner_flag(client, app):
    db = _cdb(app)
    target = db.create_user(
        "test_tenant_qa_user2",
        "pass1234",
        role="sales_executive",
        workspace_id="exec_qa2",
    )
    db.set_workspace_owner(int(target["id"]), True)

    token = _token(
        app,
        user_id=99,
        username="plain_exec",
        role="sales_executive",
        workspace_id="exec_plain",
        is_workspace_owner=False,
    )
    resp = client.put(
        f"/api/v1/admin/users/{int(target['id'])}",
        json={"is_workspace_owner": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert db.is_workspace_owner_user(int(target["id"])) is True


def test_owner_flag_mixed_with_other_fields_rejected(client, app):
    db = _cdb(app)
    owner = db.create_user(
        "owner1",
        "pass1234",
        role="sales_executive",
        workspace_id="ws1",
    )
    target = db.create_user(
        "target1",
        "pass1234",
        role="sales_executive",
        workspace_id="ws2",
    )
    db.set_workspace_owner(int(owner["id"]), True)

    token = _token(
        app,
        user_id=int(owner["id"]),
        username="owner1",
        role="sales_executive",
        workspace_id="ws1",
        is_workspace_owner=True,
    )
    resp = client.put(
        f"/api/v1/admin/users/{int(target['id'])}",
        json={"is_workspace_owner": False, "role": "retailer"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400

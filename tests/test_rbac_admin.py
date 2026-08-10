import os

import pytest
from app.jwt_service import JWTService
from app import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "rbac_admin.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")

    app = create_app()
    app.config["TESTING"] = True

    with app.app_context():
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


def make_token(app, role, username="user", workspace_id="default"):
    service = JWTService(secret_key=app.config["SECRET_KEY"])
    token, _ = service.create_tokens(user_id=1, username=username, role=role, workspace_id=workspace_id)
    return token


def test_non_admin_cannot_create_admin_user(client, app):
    token = make_token(app, role="retailer", username="retailer_user")
    response = client.post(
        "/api/v1/admin/users",
        json={"username": "newadmin", "email": "newadmin@example.com", "password": "pass1234", "role": "admin"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "FORBIDDEN"


def test_non_admin_cannot_access_database_admin(client, app):
    token = make_token(app, role="distributor", username="dist_user")
    response = client.post(
        "/admin/database",
        data={"action": "backup"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "FORBIDDEN"


def test_admin_can_create_user_and_access_database(client, app):
    token = make_token(app, role="admin", username="admin")
    response = client.post(
        "/api/v1/admin/users",
        json={"username": "newadmin2", "email": "newadmin2@example.com", "password": "pass1234", "role": "admin"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    assert response.get_json()["success"] is True

    response = client.post(
        "/admin/database",
        data={"action": "backup"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code in (200, 302)


def _seed_founder(app):
    from app.db import db
    from app.models import User

    with app.app_context():
        existing = User.query.filter_by(username="admin").first()
        if existing:
            return {"id": int(existing.id), "username": existing.username}
        founder = User(
            username="admin",
            email="founder@example.com",
            role="admin",
            status="active",
            workspace_id="default",
        )
        founder.set_password("password123")
        db.session.add(founder)
        db.session.commit()
        return {"id": int(founder.id), "username": founder.username}


def test_secondary_admin_cannot_delete_founder(client, app):
    founder = _seed_founder(app)
    from app.jwt_service import JWTService

    service = JWTService(secret_key=app.config["SECRET_KEY"])
    founder_token, _ = service.create_tokens(
        user_id=founder["id"], username="admin", role="admin", workspace_id="default"
    )

    created = client.post(
        "/api/v1/admin/users",
        json={
            "username": "second_admin",
            "email": "second@example.com",
            "password": "pass1234",
            "role": "admin",
        },
        headers={"Authorization": f"Bearer {founder_token}"},
    )
    assert created.status_code == 201, created.get_data(as_text=True)
    second = created.get_json()["data"]
    second_token, _ = service.create_tokens(
        user_id=second["id"],
        username="second_admin",
        role="admin",
        workspace_id="default",
    )

    response = client.delete(
        f"/api/v1/admin/users/{founder['id']}",
        headers={"Authorization": f"Bearer {second_token}"},
    )
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "FORBIDDEN"
    assert "Founder" in response.get_json()["error"]["message"]


def test_admin_cannot_self_delete(client, app):
    founder = _seed_founder(app)
    from app.jwt_service import JWTService

    service = JWTService(secret_key=app.config["SECRET_KEY"])
    founder_token, _ = service.create_tokens(
        user_id=founder["id"], username="admin", role="admin", workspace_id="default"
    )
    created = client.post(
        "/api/v1/admin/users",
        json={
            "username": "selfdel_admin",
            "email": "selfdel@example.com",
            "password": "pass1234",
            "role": "admin",
        },
        headers={"Authorization": f"Bearer {founder_token}"},
    )
    assert created.status_code == 201
    second = created.get_json()["data"]
    second_token, _ = service.create_tokens(
        user_id=second["id"],
        username="selfdel_admin",
        role="admin",
        workspace_id="default",
    )

    response = client.delete(
        f"/api/v1/admin/users/{second['id']}",
        headers={"Authorization": f"Bearer {second_token}"},
    )
    assert response.status_code == 403
    assert "own account" in response.get_json()["error"]["message"]


def test_secondary_admin_cannot_delete_other_admin(client, app):
    founder = _seed_founder(app)
    from app.jwt_service import JWTService

    service = JWTService(secret_key=app.config["SECRET_KEY"])
    founder_token, _ = service.create_tokens(
        user_id=founder["id"], username="admin", role="admin", workspace_id="default"
    )

    a = client.post(
        "/api/v1/admin/users",
        json={
            "username": "admin_a",
            "email": "a@example.com",
            "password": "pass1234",
            "role": "admin",
        },
        headers={"Authorization": f"Bearer {founder_token}"},
    ).get_json()["data"]
    b = client.post(
        "/api/v1/admin/users",
        json={
            "username": "admin_b",
            "email": "b@example.com",
            "password": "pass1234",
            "role": "admin",
        },
        headers={"Authorization": f"Bearer {founder_token}"},
    ).get_json()["data"]

    a_token, _ = service.create_tokens(
        user_id=a["id"], username="admin_a", role="admin", workspace_id="default"
    )
    response = client.delete(
        f"/api/v1/admin/users/{b['id']}",
        headers={"Authorization": f"Bearer {a_token}"},
    )
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "FORBIDDEN"

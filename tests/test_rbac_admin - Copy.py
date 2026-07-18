import os

import pytest
from app.jwt_service import JWTService
from app import create_app


@pytest.fixture
def app():
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["AUTH_ENABLED"] = "true"
    os.environ["SECRET_KEY"] = "test-secret"
    os.environ["ADMIN_USERNAME"] = "admin"
    os.environ["ADMIN_PASSWORD"] = "password123"

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

import importlib
import sqlite3

import pytest
from flask import Flask

from centralized_db_system.db import CentralizedDB


@pytest.fixture()
def target_app_client(tmp_path, monkeypatch):
    db_path = tmp_path / "target_achievement.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    import app.init_db as init_db_module
    import app.jwt_service as jwt_service_module
    import app.routes.auth as auth_module
    import app.web_app as web_app_module
    import app.routes.target_achievement as target_achievement_module

    importlib.reload(init_db_module)
    importlib.reload(auth_module)
    importlib.reload(jwt_service_module)
    importlib.reload(web_app_module)
    importlib.reload(target_achievement_module)

    app = web_app_module.create_app()
    app.testing = True

    db = CentralizedDB(str(db_path))
    db.create_user("user_a", "pass123", role="sales_executive", workspace_id="ws-1")
    db.create_user("user_b", "pass123", role="sales_executive", workspace_id="ws-2")

    return app.test_client()


def test_target_achievement_get_db_uses_app_config(tmp_path):
    from app.routes import target_achievement

    db_path = tmp_path / "configured_target_achievement.sqlite3"
    app = Flask(__name__)
    app.config["DATABASE_PATH"] = str(db_path)

    with app.app_context():
        conn = target_achievement.get_db()
        conn.execute("CREATE TABLE IF NOT EXISTS config_probe (id INTEGER)")
        conn.commit()
        conn.close()

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='config_probe'"
        ).fetchone()

    assert row is not None


def test_target_achievement_real_login_and_workspace_isolation(target_app_client):
    client = target_app_client

    def login(username: str, password: str) -> str:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert response.status_code == 200
        return response.get_json()["data"]["access_token"]

    token_a = login("user_a", "pass123")
    token_b = login("user_b", "pass123")

    ws1_create = client.post(
        "/api/v1/target-achievement/years",
        json={"year": "2025", "target": 1000},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert ws1_create.status_code == 201
    ws1_year = ws1_create.get_json()["data"]

    ws2_create = client.post(
        "/api/v1/target-achievement/years",
        json={"year": "2025", "target": 5000},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert ws2_create.status_code == 201

    ws1_list = client.get(
        "/api/v1/target-achievement/years",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert ws1_list.status_code == 200
    ws1_years = ws1_list.get_json()["data"]["years"]
    assert len(ws1_years) == 1
    assert ws1_years[0]["id"] == ws1_year["year_id"]
    assert ws1_years[0]["workspace_id"] == "ws-1"
    assert ws1_years[0]["target"] == 1000

    ws1_tampered_list = client.get(
        "/api/v1/target-achievement/years?workspace_id=ws-2",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert ws1_tampered_list.status_code == 200
    tampered_years = ws1_tampered_list.get_json()["data"]["years"]
    assert len(tampered_years) == 1
    assert tampered_years[0]["id"] == ws1_year["year_id"]
    assert all(year["workspace_id"] == "ws-1" for year in tampered_years)

    unauthenticated = client.get("/api/v1/target-achievement/years")
    assert unauthenticated.status_code == 401
    assert unauthenticated.get_json()["success"] is False

"""
Verifies the fix for resolve_alert (app/routes/operations.py), which
previously updated system_alerts WITHOUT any workspace_id filter —
meaning any authenticated user, from any workspace, could resolve any
other workspace's alert just by guessing/incrementing alert_id.
"""
import importlib

from centralized_db_system.db import CentralizedDB


def setup_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "alert_workspace_test.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "alert-workspace-test-key")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply_env()

    app = web_app_module.create_app()
    app.config["TESTING"] = True

    db = CentralizedDB(str(db_path))
    db.create_user("alert_user_a", "pass123", role="sales_executive", workspace_id="ws-1")

    return app.test_client(), db


def login(client, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]["access_token"]


def test_cannot_resolve_another_workspace_alert(tmp_path, monkeypatch):
    client, db = setup_auth_app(tmp_path, monkeypatch)
    token_a = login(client, "alert_user_a", "pass123")

    ws2_alert_id = db.create_alert(
        "return_claim", "ref-1", "WS2 only alert", workspace_id="ws-2"
    )

    resp = client.post(
        f"/api/v1/operations/alerts/{ws2_alert_id}/resolve",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 404, (
        f"BUG REPRODUCED: ws-1 was able to resolve ws-2's alert. "
        f"Status: {resp.status_code}"
    )

    ws2_alerts = db.list_alerts(workspace_id="ws-2")
    assert ws2_alerts[0]["resolved"] is False, "ws-2's alert should remain unresolved"


def test_can_resolve_own_workspace_alert(tmp_path, monkeypatch):
    client, db = setup_auth_app(tmp_path, monkeypatch)
    token_a = login(client, "alert_user_a", "pass123")

    own_alert_id = db.create_alert(
        "return_claim", "ref-2", "WS1 own alert", workspace_id="ws-1"
    )

    resp = client.post(
        f"/api/v1/operations/alerts/{own_alert_id}/resolve",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 200

    ws1_alerts = db.list_alerts(workspace_id="ws-1")
    assert ws1_alerts[0]["resolved"] is True

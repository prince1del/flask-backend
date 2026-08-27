import importlib
import io
import uuid

from centralized_db_system.db import CentralizedDB
from app.web_app import create_app


def setup_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "workspace_isolation.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "workspace-isolation-test-key")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    # Reloading web_app re-runs its top-level load_env_file(), which reads the
    # real .env and overwrites these overrides — re-apply them afterwards.
    importlib.reload(web_app_module)
    _apply_env()

    app = web_app_module.create_app()
    app.config["TESTING"] = True

    db = CentralizedDB(str(db_path))
    db.create_user("workspace_user_a", "pass123", role="sales_executive", workspace_id="ws-1")
    db.create_user("workspace_user_b", "pass123", role="sales_executive", workspace_id="ws-2")

    return app.test_client()


def _user_id(db: CentralizedDB, username: str) -> int:
    import sqlite3

    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
    assert row is not None, f"user {username} not found"
    return int(row[0])


def login(client, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]["access_token"]


def create_distributor(client, token: str, name: str, gst_number: str):
    response = client.post(
        "/api/v1/parties/distributors",
        json={"name": name, "gst_number": gst_number},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()["data"]


def create_retailer(client, token: str, distributor_id: int, name: str, gst_number: str):
    response = client.post(
        "/api/v1/parties/retailers",
        json={"name": name, "distributor_id": distributor_id, "gst_number": gst_number},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()["data"]


def test_two_workspaces_do_not_mix_master_party_data(tmp_path, monkeypatch):
    client = setup_auth_app(tmp_path, monkeypatch)

    token_a = login(client, "workspace_user_a", "pass123")
    token_b = login(client, "workspace_user_b", "pass123")

    dist_a = create_distributor(client, token_a, "Distributor WS1", "GST-WS1")
    dist_b = create_distributor(client, token_b, "Distributor WS2", "GST-WS2")

    retailer_a = create_retailer(client, token_a, dist_a["id"], "Retailer WS1", "GST-RT-WS1")
    retailer_b = create_retailer(client, token_b, dist_b["id"], "Retailer WS2", "GST-RT-WS2")

    get_dist_a = client.get(
        f"/api/v1/parties/distributors/{dist_a['id']}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert get_dist_a.status_code == 200
    assert get_dist_a.get_json()["data"]["workspace_id"] == "ws-1"

    get_dist_b_wrong = client.get(
        f"/api/v1/parties/distributors/{dist_b['id']}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert get_dist_b_wrong.status_code == 404

    get_retailer_a = client.get(
        f"/api/v1/parties/retailers/{retailer_a['id']}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert get_retailer_a.status_code == 200
    assert get_retailer_a.get_json()["data"]["workspace_id"] == "ws-1"

    get_retailer_b_wrong = client.get(
        f"/api/v1/parties/retailers/{retailer_b['id']}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert get_retailer_b_wrong.status_code == 404

    list_a_dist = client.get(
        "/api/v1/parties/distributors",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert list_a_dist.status_code == 200
    assert list_a_dist.get_json()["data"]["count"] == 1
    assert list_a_dist.get_json()["data"]["results"][0]["id"] == dist_a["id"]

    list_b_dist = client.get(
        "/api/v1/parties/distributors",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert list_b_dist.status_code == 200
    assert list_b_dist.get_json()["data"]["count"] == 1
    assert list_b_dist.get_json()["data"]["results"][0]["id"] == dist_b["id"]

    list_a_retailer = client.get(
        "/api/v1/parties/retailers",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert list_a_retailer.status_code == 200
    assert list_a_retailer.get_json()["data"]["count"] == 1
    assert list_a_retailer.get_json()["data"]["results"][0]["id"] == retailer_a["id"]

    list_b_retailer = client.get(
        "/api/v1/parties/retailers",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert list_b_retailer.status_code == 200
    assert list_b_retailer.get_json()["data"]["count"] == 1
    assert list_b_retailer.get_json()["data"]["results"][0]["id"] == retailer_b["id"]


def test_master_tables_workspace_isolation_and_analytics_dashboard(tmp_path, monkeypatch):
    client = setup_auth_app(tmp_path, monkeypatch)

    token_a = login(client, "workspace_user_a", "pass123")
    token_b = login(client, "workspace_user_b", "pass123")

    db = CentralizedDB(str(tmp_path / "workspace_isolation.sqlite3"))

    # Party rows are owned by a user_id, not just a workspace.
    user_a_id = _user_id(db, "workspace_user_a")
    user_b_id = _user_id(db, "workspace_user_b")

    dist_a_id = db.add_master_distributor(
        name="Master WS1 Distributor",
        firm_name="Master WS1",
        gst_no="GST-WS1-MASTER",
        workspace_id="ws-1",
        user_id=user_a_id,
    )
    dist_b_id = db.add_master_distributor(
        name="Master WS2 Distributor",
        firm_name="Master WS2",
        gst_no="GST-WS2-MASTER",
        workspace_id="ws-2",
        user_id=user_b_id,
    )

    retailer_a_id = db.add_master_retailer(
        name="Master WS1 Retailer",
        distributor_id=dist_a_id,
        location="Mumbai",
        gst_no="GST-RT-WS1-MASTER",
        workspace_id="ws-1",
        user_id=user_a_id,
    )
    retailer_b_id = db.add_master_retailer(
        name="Master WS2 Retailer",
        distributor_id=dist_b_id,
        location="Pune",
        gst_no="GST-RT-WS2-MASTER",
        workspace_id="ws-2",
        user_id=user_b_id,
    )

    distributors_ws1 = db.list_master_distributors(workspace_id="ws-1", user_id=user_a_id)
    distributors_ws2 = db.list_master_distributors(workspace_id="ws-2", user_id=user_b_id)
    assert len(distributors_ws1) == 1
    assert distributors_ws1[0]["id"] == dist_a_id
    assert len(distributors_ws2) == 1
    assert distributors_ws2[0]["id"] == dist_b_id

    retailers_ws1 = db.list_master_retailers(workspace_id="ws-1", user_id=user_a_id)
    retailers_ws2 = db.list_master_retailers(workspace_id="ws-2", user_id=user_b_id)
    assert len(retailers_ws1) == 1
    assert retailers_ws1[0]["id"] == retailer_a_id
    assert len(retailers_ws2) == 1
    assert retailers_ws2[0]["id"] == retailer_b_id

    dashboard_payload_ws1 = db.get_dashboard_payload(workspace_id="ws-1")
    dashboard_payload_ws2 = db.get_dashboard_payload(workspace_id="ws-2")
    assert dashboard_payload_ws1["masters"]["distributors"] == 1
    assert dashboard_payload_ws1["masters"]["retailers"] == 1
    assert dashboard_payload_ws2["masters"]["distributors"] == 1
    assert dashboard_payload_ws2["masters"]["retailers"] == 1

    response_a = client.get(
        "/analytics",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert response_a.status_code == 200
    html_a = response_a.get_data(as_text=True)
    assert "Target coverage" in html_a
    assert "1 distributors active" in html_a
    assert "Retailer Snapshot" in html_a

    response_b = client.get(
        "/analytics",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response_b.status_code == 200
    html_b = response_b.get_data(as_text=True)
    assert "Target coverage" in html_b
    assert "1 distributors active" in html_b
    assert "Retailer Snapshot" in html_b




def test_business_brain_requires_auth_for_api_without_token(tmp_path, monkeypatch):
    """POST to /api/business/conversation without Authorization must be rejected (401)."""
    client = setup_auth_app(tmp_path, monkeypatch)

    resp = client.post('/api/business/conversation', json={'title': 'NoAuth'})
    assert resp.status_code == 401


def test_gdrive_does_not_silently_use_default_user_when_missing(tmp_path, monkeypatch):
    """When request.user is missing, gdrive endpoints must NOT silently use user_id=1, must return 401."""
    client = setup_auth_app(tmp_path, monkeypatch)

    # Call the connect endpoint WITHOUT Authorization header.
    # Previously code would silently default to user_id=1 and succeed.
    # Now it should return 401 with authentication error.
    resp = client.get('/api/gdrive/connect/1')
    
    # Must NOT be 200 (success) - should be 401 or error
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code} (body: {resp.get_data(as_text=True)})"
    
    # Response should indicate missing auth (either from decorator or from our handler)
    json_data = resp.get_json() or {}
    error_info = str(json_data)
    
    # Check for either NO_TOKEN (from @require_jwt_auth) or Authentication required (from handler)
    assert 'NO_TOKEN' in error_info or 'Authentication required' in error_info, \
        f"Expected auth error, got: {error_info}"

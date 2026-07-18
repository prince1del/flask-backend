"""
Verifies the new Company Profile feature:
  1. Workspace isolation — each workspace's own company identity
     (name, GST, etc.) stays completely separate from every other
     workspace's, exactly like every other workspace-scoped table.
  2. Role restriction — only admin/sales_executive roles can access
     it; this is deliberately future-proofed so that when
     distributor/retailer-type logins are introduced later, they are
     automatically excluded by default (require_role uses an
     allow-list, not a block-list).
"""
import importlib

from centralized_db_system.db import CentralizedDB


def setup_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "company_profile_test.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "company-profile-test-key")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply_env()

    app = web_app_module.create_app()
    app.config["TESTING"] = True

    db = CentralizedDB(str(db_path))
    db.create_user("exec_ws1", "pass123", role="sales_executive", workspace_id="ws-1")
    db.create_user("exec_ws2", "pass123", role="sales_executive", workspace_id="ws-2")
    db.create_user("unassigned_user", "pass123", role="unassigned", workspace_id="ws-1")

    return app.test_client(), db


def login(client, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]["access_token"]


def test_workspace_can_save_and_retrieve_own_company_profile(tmp_path, monkeypatch):
    client, _db = setup_auth_app(tmp_path, monkeypatch)
    token = login(client, "exec_ws1", "pass123")

    save_resp = client.post(
        "/api/v1/company-profile",
        json={
            "company_name": "Bombay Dyeing",
            "gst_number": "27aaact2328k1zb",  # lowercase on purpose — should be normalized
            "city": "Mumbai",
            "state": "Maharashtra",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert save_resp.status_code == 200
    saved = save_resp.get_json()["data"]
    assert saved["company_name"] == "Bombay Dyeing"
    assert saved["gst_number"] == "27AAACT2328K1ZB", "GST should be normalized to uppercase"

    get_resp = client.get(
        "/api/v1/company-profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_resp.status_code == 200
    assert get_resp.get_json()["data"]["company_name"] == "Bombay Dyeing"


def test_workspaces_have_completely_isolated_company_profiles(tmp_path, monkeypatch):
    client, _db = setup_auth_app(tmp_path, monkeypatch)
    token_1 = login(client, "exec_ws1", "pass123")
    token_2 = login(client, "exec_ws2", "pass123")

    client.post(
        "/api/v1/company-profile",
        json={"company_name": "Bombay Dyeing", "gst_number": "27AAACT2328K1ZB"},
        headers={"Authorization": f"Bearer {token_1}"},
    )
    client.post(
        "/api/v1/company-profile",
        json={"company_name": "Some Other Textile Co", "gst_number": "19BBBBB1234C1Z1"},
        headers={"Authorization": f"Bearer {token_2}"},
    )

    ws1_view = client.get(
        "/api/v1/company-profile", headers={"Authorization": f"Bearer {token_1}"}
    ).get_json()["data"]
    ws2_view = client.get(
        "/api/v1/company-profile", headers={"Authorization": f"Bearer {token_2}"}
    ).get_json()["data"]

    assert ws1_view["company_name"] == "Bombay Dyeing"
    assert ws2_view["company_name"] == "Some Other Textile Co"
    assert ws1_view["gst_number"] != ws2_view["gst_number"]


def test_unassigned_role_cannot_access_company_profile(tmp_path, monkeypatch):
    """
    Future-proofing check: require_role() uses an allow-list
    (admin, sales_executive only) — any role NOT on that list,
    including a role that doesn't exist yet (like a future
    'distributor' role), is blocked by default. 'unassigned' stands in
    here for "any role we haven't explicitly granted access to".
    """
    client, _db = setup_auth_app(tmp_path, monkeypatch)
    token = login(client, "unassigned_user", "pass123")

    resp = client.get(
        "/api/v1/company-profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_missing_company_name_is_rejected(tmp_path, monkeypatch):
    client, _db = setup_auth_app(tmp_path, monkeypatch)
    token = login(client, "exec_ws1", "pass123")

    resp = client.post(
        "/api/v1/company-profile",
        json={"gst_number": "27AAACT2328K1ZB"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400

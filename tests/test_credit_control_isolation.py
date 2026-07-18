"""
Verifies workspace isolation for credit_control — the highest-severity
gap found in this table, because unlike read-only leaks elsewhere,
this table is WRITABLE via /credit-policy. Before this fix, any
authenticated user (from any workspace) could change ANY distributor's
credit limit or account status just by knowing/guessing a distributor_id.
"""
import importlib

from centralized_db_system.db import CentralizedDB


def setup_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "credit_control_test.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "credit-control-test-key")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    # web_app's load_env_file() re-reads the real .env on reload and can
    # clobber these monkeypatched values — re-apply after reload.
    _apply_env()

    app = web_app_module.create_app()
    app.config["TESTING"] = True

    db = CentralizedDB(str(db_path))
    db.create_user("credit_user_a", "pass123", role="sales_executive", workspace_id="ws-1")
    db.create_user("credit_user_b", "pass123", role="sales_executive", workspace_id="ws-2")

    return app.test_client(), db


def login(client, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]["access_token"]


def test_credit_policy_requires_auth(tmp_path, monkeypatch):
    client, _db = setup_auth_app(tmp_path, monkeypatch)
    resp = client.get("/credit-policy")
    assert resp.status_code in (401, 302)


def test_user_cannot_view_other_workspace_credit_data(tmp_path, monkeypatch):
    client, db = setup_auth_app(tmp_path, monkeypatch)

    token_a = login(client, "credit_user_a", "pass123")
    token_b = login(client, "credit_user_b", "pass123")

    dist_a_id = db.add_master_distributor(
        name="WS1 Distributor", firm_name="WS1 Firm", gst_no="GST-WS1", workspace_id="ws-1"
    )
    dist_b_id = db.add_master_distributor(
        name="WS2 Distributor", firm_name="WS2 Firm", gst_no="GST-WS2", workspace_id="ws-2"
    )

    db.upsert_credit_control(
        dist_a_id, max_credit_limit=100000, credit_days_allowed=30,
        account_status="ACTIVE", workspace_id="ws-1",
    )
    db.upsert_credit_control(
        dist_b_id, max_credit_limit=500000, credit_days_allowed=60,
        account_status="ACTIVE", workspace_id="ws-2",
    )

    # ws-1's view of /credit-policy must show only its own distributor's
    # credit data, not ws-2's.
    resp_a = client.get(
        "/credit-policy", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert resp_a.status_code == 200
    body_a = resp_a.get_data(as_text=True)
    assert "100000" in body_a
    assert "500000" not in body_a, "ws-1 should not see ws-2's credit limit"


def test_user_cannot_modify_other_workspace_distributor_credit(tmp_path, monkeypatch):
    """
    THE CRITICAL ATTACK SCENARIO: a ws-1 user submits a POST to
    /credit-policy with ws-2's distributor_id, attempting to grant
    themselves (or that distributor) an inflated credit limit outside
    their own workspace. This must be rejected, and ws-2's real data
    must remain unchanged.
    """
    client, db = setup_auth_app(tmp_path, monkeypatch)

    token_a = login(client, "credit_user_a", "pass123")

    dist_b_id = db.add_master_distributor(
        name="WS2 Distributor", firm_name="WS2 Firm", gst_no="GST-WS2", workspace_id="ws-2"
    )
    db.upsert_credit_control(
        dist_b_id, max_credit_limit=500000, credit_days_allowed=60,
        account_status="ACTIVE", workspace_id="ws-2",
    )

    # ws-1's token tries to grant ws-2's distributor an unlimited credit limit
    attack_resp = client.post(
        "/credit-policy",
        data={
            "distributor_id": str(dist_b_id),
            "max_credit_limit": "99999999",
            "credit_days_allowed": "9999",
            "account_status": "ACTIVE",
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert attack_resp.status_code == 200
    assert "not found in your workspace" in attack_resp.get_data(as_text=True).lower()

    # Confirm ws-2's actual credit_control row was NOT modified by the attack
    unchanged = db.list_credit_control(workspace_id="ws-2")
    assert len(unchanged) == 1
    assert unchanged[0]["max_credit_limit"] == 500000, (
        "Attack succeeded! ws-2's credit limit was modified by a ws-1 user: "
        f"{unchanged}"
    )

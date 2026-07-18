"""
Verifies workspace isolation for the 4 remaining tables found via the
_table_has_column(..., "workspace_id") audit:
  - data_entry_alert_logs  (/alerts)
  - workflow_todo_list     (/workflow-gps, dashboard summary)
  - gps_visit_verification_logs (/workflow-gps)
  - distributor_purchase_behavior_logs (/purchase-behavior, ownership-checked
    via master_distributors rather than needing its own workspace_id column)
"""
import importlib

from centralized_db_system.db import CentralizedDB


def setup_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "remaining_tables_test.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "remaining-tables-test-key")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply_env()  # re-apply after reload; load_env_file() clobbers these otherwise

    app = web_app_module.create_app()
    app.config["TESTING"] = True

    db = CentralizedDB(str(db_path))
    db.create_user("remtab_user_a", "pass123", role="sales_executive", workspace_id="ws-1")
    db.create_user("remtab_user_b", "pass123", role="sales_executive", workspace_id="ws-2")

    return app.test_client(), db


def login(client, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]["access_token"]


def test_alerts_page_does_not_show_other_workspace_alerts(tmp_path, monkeypatch):
    client, db = setup_auth_app(tmp_path, monkeypatch)
    token_a = login(client, "remtab_user_a", "pass123")

    db.create_data_entry_alert(
        "invoice", "REF-WS1", {"amount": 100}, ["warn ws1"], workspace_id="ws-1"
    )
    db.create_data_entry_alert(
        "invoice", "REF-WS2", {"amount": 200}, ["warn ws2"], workspace_id="ws-2"
    )

    resp = client.get("/alerts", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "REF-WS1" in body
    assert "REF-WS2" not in body, "ws-1 should not see ws-2's data entry alert"


def test_workflow_gps_page_does_not_show_other_workspace_tasks_or_gps_logs(tmp_path, monkeypatch):
    client, db = setup_auth_app(tmp_path, monkeypatch)
    token_a = login(client, "remtab_user_a", "pass123")

    db.create_workflow_todo_task(
        staff_id=1, party_id=1, party_type="distributor",
        task_description="WS1 TASK MARKER", workspace_id="ws-1",
    )
    db.create_workflow_todo_task(
        staff_id=1, party_id=1, party_type="distributor",
        task_description="WS2 TASK MARKER", workspace_id="ws-2",
    )
    db.record_gps_visit_verification(
        visit_log_id=1, captured_latitude=19.0760, captured_longitude=72.8777,
        device_timestamp="2026-07-04T10:00:00Z", workspace_id="ws-1",
    )
    db.record_gps_visit_verification(
        visit_log_id=2, captured_latitude=18.5204, captured_longitude=73.8567,
        device_timestamp="2026-07-04T11:00:00Z", workspace_id="ws-2",
    )

    resp = client.get(
        "/workflow-gps?party_id=1&party_type=distributor",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    assert "WS1 TASK MARKER" in body
    assert "WS2 TASK MARKER" not in body, "ws-1 should not see ws-2's workflow task"

    # GPS coordinates are the more sensitive payload here: ws-2's device
    # timestamp acts as a unique marker for its GPS log row.
    assert "2026-07-04T10:00:00Z" in body
    assert "2026-07-04T11:00:00Z" not in body, "ws-1 should not see ws-2's GPS visit log"


def test_purchase_behavior_rejects_cross_workspace_distributor_id(tmp_path, monkeypatch):
    client, db = setup_auth_app(tmp_path, monkeypatch)
    token_a = login(client, "remtab_user_a", "pass123")

    dist_b_id = db.add_master_distributor(
        name="WS2 Distributor", firm_name="WS2 Firm", gst_no="GST-WS2", workspace_id="ws-2"
    )

    # ws-1's token tries to view ws-2's distributor's purchase behavior
    resp = client.get(
        f"/purchase-behavior?distributor_id={dist_b_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 404, (
        f"Expected 404 when ws-1 requests ws-2's distributor_id, got "
        f"{resp.status_code}: {resp.get_data(as_text=True)}"
    )


def test_purchase_behavior_requires_auth_and_a_distributor_id(tmp_path, monkeypatch):
    client, _db = setup_auth_app(tmp_path, monkeypatch)

    # No auth at all
    resp_no_auth = client.get("/purchase-behavior?distributor_id=1")
    assert resp_no_auth.status_code in (401, 302)

    token_a = login(client, "remtab_user_a", "pass123")
    # No distributor_id provided — must not silently default to distributor 1
    # (the old behavior), it must require an explicit id.
    resp_missing_id = client.get(
        "/purchase-behavior", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert resp_missing_id.status_code == 400

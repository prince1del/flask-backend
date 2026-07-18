import importlib
import io
import json
from pathlib import Path

from centralized_db_system.db import CentralizedDB
from app.web_app import create_app


def setup_app(tmp_path, monkeypatch):
    db_path = tmp_path / "order_sheet_master.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)

    app = web_app_module.create_app()
    app.config["TESTING"] = True
    return app.test_client()


def create_user(client, username: str, password: str):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    return response


def test_order_sheet_master_crud_and_workspace_isolation(tmp_path, monkeypatch):
    client = setup_app(tmp_path, monkeypatch)
    db_path = tmp_path / "order_sheet_master.sqlite3"
    db = CentralizedDB(str(db_path))

    # Prepare authenticated workspace users
    db.create_user("user_ws_1", "pass123", role="sales_executive", workspace_id="ws-1")
    db.create_user("user_ws_2", "pass123", role="sales_executive", workspace_id="ws-2")

    # Login to acquire token for ws-1
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": "user_ws_1", "password": "pass123"},
    )
    assert login_resp.status_code == 200
    token = login_resp.get_json()["data"]["access_token"]

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Create order sheet in ws-1
    create_resp = client.post(
        "/api/v1/order-sheets",
        json={
            "name": "AW26 Bedsheet",
            "category": "Bedsheet",
            "file_reference": "/tmp/bedsheet.xlsx",
            "is_active": 1,
        },
        headers=headers,
    )
    assert create_resp.status_code == 201
    created = create_resp.get_json()["data"]
    assert created["name"] == "AW26 Bedsheet"
    assert created["category"] == "Bedsheet"
    assert created["workspace_id"] == "ws-1"
    assert created["is_active"] == 1

    sheet_id = created["id"]

    # ws-1 can list its own order sheets
    list_resp = client.get("/api/v1/order-sheets", headers=headers)
    assert list_resp.status_code == 200
    list_data = list_resp.get_json()["data"]
    assert len(list_data) == 1
    assert list_data[0]["id"] == sheet_id

    # Another workspace should not see ws-1 order sheets
    login_resp2 = client.post(
        "/api/v1/auth/login",
        json={"username": "user_ws_2", "password": "pass123"},
    )
    assert login_resp2.status_code == 200
    token2 = login_resp2.get_json()["data"]["access_token"]
    headers2 = {"Authorization": f"Bearer {token2}", "Content-Type": "application/json"}

    list_resp2 = client.get("/api/v1/order-sheets", headers=headers2)
    assert list_resp2.status_code == 200
    assert list_resp2.get_json()["data"] == []

    # ws-1 can update active status
    update_resp = client.put(
        f"/api/v1/order-sheets/{sheet_id}/status",
        json={"is_active": 0},
        headers=headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.get_json()["data"]["is_active"] == 0

    # ws-1 can retrieve specific sheet
    get_resp = client.get(f"/api/v1/order-sheets/{sheet_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.get_json()["data"]["id"] == sheet_id

    # ws-2 should not access ws-1 sheet by id
    forbidden_resp = client.get(f"/api/v1/order-sheets/{sheet_id}", headers=headers2)
    assert forbidden_resp.status_code == 404


def test_repeated_order_sheet_uploads_create_history_entries(tmp_path, monkeypatch):
    client = setup_app(tmp_path, monkeypatch)
    db_path = tmp_path / "order_sheet_master.sqlite3"
    db = CentralizedDB(str(db_path))

    db.create_user("user_ws_history", "pass123", role="sales_executive", workspace_id="ws-history")

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": "user_ws_history", "password": "pass123"},
    )
    assert login_resp.status_code == 200
    token = login_resp.get_json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    first_upload = client.post(
        "/legacy",
        data={
            "workflow_action": "stage1",
            "order_sheet_name": "AW26 Bedsheet",
            "order_sheet_category": "Bedsheet",
            "order_sheet_is_active": "1",
            "order_file": (io.BytesIO(b"product,qty,rate\nSheet A,10,1200\n"), "sample1.csv"),
        },
        headers=headers,
    )
    assert first_upload.status_code == 200

    second_upload = client.post(
        "/legacy",
        data={
            "workflow_action": "stage1",
            "order_sheet_name": "AW26 Bedsheet Revised",
            "order_sheet_category": "Bedsheet",
            "order_sheet_is_active": "1",
            "order_file": (io.BytesIO(b"product,qty,rate\nSheet A,10,1200\n"), "sample2.csv"),
        },
        headers=headers,
    )
    assert second_upload.status_code == 200

    sheets = db.list_order_sheets(workspace_id="ws-history")
    assert len(sheets) == 1
    assert sheets[0]["name"] == "AW26 Bedsheet"


def test_different_content_uploads_can_create_new_history_entry(tmp_path, monkeypatch):
    client = setup_app(tmp_path, monkeypatch)
    db_path = tmp_path / "order_sheet_master.sqlite3"
    db = CentralizedDB(str(db_path))

    db.create_user("user_ws_history_2", "pass123", role="sales_executive", workspace_id="ws-history-2")

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": "user_ws_history_2", "password": "pass123"},
    )
    assert login_resp.status_code == 200
    token = login_resp.get_json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    first_upload = client.post(
        "/legacy",
        data={
            "workflow_action": "stage1",
            "order_sheet_name": "AW26 Bedsheet",
            "order_sheet_category": "Bedsheet",
            "order_sheet_is_active": "1",
            "order_file": (io.BytesIO(b"product,qty,rate\nSheet A,10,1200\n"), "sample1.csv"),
        },
        headers=headers,
    )
    assert first_upload.status_code == 200

    second_upload = client.post(
        "/legacy",
        data={
            "workflow_action": "stage1",
            "order_sheet_name": "AW26 Bedsheet Revised",
            "order_sheet_category": "Bedsheet",
            "order_sheet_is_active": "1",
            "order_file": (io.BytesIO(b"product,qty,rate\nSheet A,10,1300\n"), "sample2.csv"),
        },
        headers=headers,
    )
    assert second_upload.status_code == 200

    sheets = db.list_order_sheets(workspace_id="ws-history-2")
    assert len(sheets) == 2
    assert {sheet["name"] for sheet in sheets} == {"AW26 Bedsheet", "AW26 Bedsheet Revised"}

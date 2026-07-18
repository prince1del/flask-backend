"""
Verifies workspace isolation for custom_schema_fields — the Schema
Manager. Previously ALL workspaces shared one global schema
(UNIQUE(entity_type, field_name)); confirmed with the founder that
different workspaces/executives need their own custom fields
(e.g. bedsheet business vs parlour business), so each workspace must
now be able to define its own fields without colliding with another
workspace's field names.
"""
import importlib

from centralized_db_system.db import CentralizedDB


def setup_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "schema_fields_test.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "schema-fields-test-key")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply_env()

    app = web_app_module.create_app()
    app.config["TESTING"] = True

    db = CentralizedDB(str(db_path))
    # user_a is the founder/admin; user_b is a regular executive in a
    # different workspace, to prove non-admins are still correctly
    # blocked from write routes while both can view their own workspace.
    db.create_user("schema_admin", "pass123", role="admin", workspace_id="ws-1")
    db.create_user("schema_user_b", "pass123", role="sales_executive", workspace_id="ws-2")

    return app.test_client(), db


def login(client, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]["access_token"]


def test_two_workspaces_can_use_the_same_field_name_without_colliding(tmp_path, monkeypatch):
    """
    This is the concrete business case: a bedsheet workspace wants a
    'fabric_type' field, and a parlour workspace ALSO wants a
    'fabric_type'-equivalent field with the same technical name.
    Before this fix, the global UNIQUE(entity_type, field_name)
    constraint made this impossible.
    """
    client, db = setup_auth_app(tmp_path, monkeypatch)

    id_ws1 = db.add_schema_field(
        "distributor", "fabric_type", "Fabric Type", "text", 0, workspace_id="ws-1"
    )
    id_ws2 = db.add_schema_field(
        "distributor", "fabric_type", "Service Category", "text", 0, workspace_id="ws-2"
    )

    assert id_ws1 and id_ws2, "Both workspaces should be able to create a field named 'fabric_type'"

    fields_ws1 = db.get_all_schema_fields("distributor", workspace_id="ws-1")
    fields_ws2 = db.get_all_schema_fields("distributor", workspace_id="ws-2")

    assert len(fields_ws1) == 1
    assert fields_ws1[0]["field_label"] == "Fabric Type"
    assert len(fields_ws2) == 1
    assert fields_ws2[0]["field_label"] == "Service Category"


def test_schema_manager_page_does_not_show_other_workspace_fields(tmp_path, monkeypatch):
    client, db = setup_auth_app(tmp_path, monkeypatch)
    token_admin = login(client, "schema_admin", "pass123")

    db.add_schema_field(
        "distributor", "bedsheet_marker_field", "Bedsheet Marker", "text", 0, workspace_id="ws-1"
    )
    db.add_schema_field(
        "distributor", "parlour_marker_field", "Parlour Marker", "text", 0, workspace_id="ws-2"
    )

    resp = client.get(
        "/settings/schema?entity=distributor",
        headers={"Authorization": f"Bearer {token_admin}"},
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Bedsheet Marker" in body
    assert "Parlour Marker" not in body, "ws-1 admin should not see ws-2's custom fields"


def test_non_admin_cannot_add_schema_field(tmp_path, monkeypatch):
    client, _db = setup_auth_app(tmp_path, monkeypatch)
    token_b = login(client, "schema_user_b", "pass123")

    resp = client.post(
        "/settings/schema/add",
        data={"entity": "distributor", "field_name": "hack_field", "field_label": "Hack"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 403


def test_admin_cannot_delete_another_workspace_field_by_guessing_id(tmp_path, monkeypatch):
    """
    Even though only admins can reach the delete route, an admin token
    is scoped to their OWN workspace (ws-1) — they should not be able
    to delete a field belonging to ws-2 just by guessing its id.
    """
    client, db = setup_auth_app(tmp_path, monkeypatch)
    token_admin = login(client, "schema_admin", "pass123")

    ws2_field_id = db.add_schema_field(
        "distributor", "ws2_only_field", "WS2 Only", "text", 0, workspace_id="ws-2"
    )

    client.post(
        "/settings/schema/delete",
        data={"entity": "distributor", "field_id": str(ws2_field_id)},
        headers={"Authorization": f"Bearer {token_admin}"},
    )

    # Confirm ws-2's field still exists (delete should have been scoped
    # to ws-1 and silently affected 0 rows)
    remaining = db.get_all_schema_fields("distributor", workspace_id="ws-2")
    assert any(f["id"] == ws2_field_id for f in remaining), (
        "ws-1's admin was able to delete a field belonging to ws-2"
    )

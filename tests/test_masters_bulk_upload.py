"""
Verifies fixes to CentralizedDB.bulk_upload_masters() and the newly
exposed API routes:

1. workspace_id was hardcoded to "default" — fixed to accept a real
   parameter, matching every other workspace-scoped function in this
   project.
2. Retailer-distributor linking previously either auto-created a new
   distributor from raw text (with NO confirmation) or silently
   skipped/discarded the retailer entirely if the reference was blank
   — replaced with: exact match -> fuzzy (typo-tolerant) match against
   EXISTING distributors only -> "Unassigned" fallback (never
   auto-create, never discard the retailer's data).
3. Export functions previously had no workspace_id filtering at all
   (a cross-tenant data leak risk) — fixed.
4. Retailer export now supports distributor-wise filtering.
"""
import importlib
import io

import openpyxl

from centralized_db_system.db import CentralizedDB


def setup_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "masters_bulk_test.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "masters-bulk-test-key")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply_env()

    app = web_app_module.create_app()
    app.config["TESTING"] = True

    db = CentralizedDB(str(db_path))
    db.create_user("masters_user_a", "pass123", role="sales_executive", workspace_id="ws-1")
    db.create_user("masters_user_b", "pass123", role="sales_executive", workspace_id="ws-2")

    return app.test_client(), db, db_path


def _user_id(db: CentralizedDB, username: str) -> int:
    """Master rows are owned by a user_id, not just a workspace."""
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


def _make_xlsx(rows: list[dict], columns: list[str]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(columns)
    for row in rows:
        ws.append([row.get(col, "") for col in columns])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def test_bulk_upload_distributors_respects_workspace_id(tmp_path, monkeypatch):
    """BUG REPRODUCED (before fix): bulk_upload_masters always wrote
    to workspace_id='default' regardless of who called it."""
    client, db, _db_path = setup_auth_app(tmp_path, monkeypatch)
    token = login(client, "masters_user_a", "pass123")

    xlsx_bytes = _make_xlsx(
        [{"Distributor Name": "Test Distributor WS1"}],
        ["Distributor Name"],
    )
    resp = client.post(
        "/api/v1/masters/distributors/bulk-upload",
        data={"file": (io.BytesIO(xlsx_bytes), "dist.xlsx")},
        headers={"Authorization": f"Bearer {token}"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["data"]["inserted"] == 1

    ws1_distributors = db.list_master_distributors(
        workspace_id="ws-1", user_id=_user_id(db, "masters_user_a")
    )
    default_distributors = db.list_master_distributors(
        workspace_id="default", user_id=_user_id(db, "masters_user_a")
    )
    assert any(d["name"] == "Test Distributor WS1" for d in ws1_distributors), (
        "Uploaded distributor should land in the CALLER's workspace (ws-1)"
    )
    assert not any(d["name"] == "Test Distributor WS1" for d in default_distributors), (
        "BUG REPRODUCED: distributor incorrectly landed in 'default' workspace"
    )


def test_retailer_fuzzy_matches_distributor_nickname_with_typo(tmp_path, monkeypatch):
    """The exact real-world scenario described: a retailer file says
    'Benrina' (a typo of 'Bernina') and must still correctly link to
    the existing 'Bernina International P Ltd' distributor."""
    client, db, _db_path = setup_auth_app(tmp_path, monkeypatch)
    token = login(client, "masters_user_a", "pass123")

    db.add_master_distributor(
        name="Bernina International P Ltd",
        workspace_id="ws-1",
        user_id=_user_id(db, "masters_user_a"),
    )

    xlsx_bytes = _make_xlsx(
        [{"Retailer Name": "Local Shop", "Distributor": "Benrina"}],
        ["Retailer Name", "Distributor"],
    )
    resp = client.post(
        "/api/v1/masters/retailers/bulk-upload",
        data={"file": (io.BytesIO(xlsx_bytes), "ret.xlsx")},
        headers={"Authorization": f"Bearer {token}"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    result = resp.get_json()["data"]
    assert result["inserted"] == 1
    assert result["unassigned"] == 0, (
        f"Typo 'Benrina' should have fuzzy-matched to Bernina, not gone Unassigned: {result}"
    )

    retailers = db.list_master_retailers(
        workspace_id="ws-1", user_id=_user_id(db, "masters_user_a")
    )
    assert len(retailers) == 1
    assert retailers[0]["distributor_id"] is not None


def test_retailer_with_no_distributor_match_is_unassigned_not_discarded(tmp_path, monkeypatch):
    """BUG REPRODUCED (before fix): a retailer with no matching
    distributor was either silently skipped (data lost) or a brand
    new, low-quality distributor was auto-created from raw text with
    no confirmation. Correct behavior: save as Unassigned, keep the
    retailer visible."""
    client, db, _db_path = setup_auth_app(tmp_path, monkeypatch)
    token = login(client, "masters_user_a", "pass123")

    xlsx_bytes = _make_xlsx(
        [{"Retailer Name": "Orphan Shop", "Distributor": "Completely Unknown Xyz Corp"}],
        ["Retailer Name", "Distributor"],
    )
    resp = client.post(
        "/api/v1/masters/retailers/bulk-upload",
        data={"file": (io.BytesIO(xlsx_bytes), "ret.xlsx")},
        headers={"Authorization": f"Bearer {token}"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    result = resp.get_json()["data"]
    assert result["inserted"] == 1, "Retailer must still be saved, not discarded"
    assert result["unassigned"] == 1

    retailers = db.list_master_retailers(
        workspace_id="ws-1", user_id=_user_id(db, "masters_user_a")
    )
    assert len(retailers) == 1
    assert retailers[0]["name"] == "Orphan Shop"
    assert retailers[0]["distributor_id"] is None, "Should be Unassigned, not auto-linked/created"

    # No new distributor should have been silently created either.
    distributors = db.list_master_distributors(
        workspace_id="ws-1", user_id=_user_id(db, "masters_user_a")
    )
    assert not any("Unknown Xyz" in (d.get("name") or "") for d in distributors), (
        "BUG REPRODUCED: a new distributor was silently auto-created from free text"
    )


def test_export_distributors_is_workspace_isolated(tmp_path, monkeypatch):
    client, db, _db_path = setup_auth_app(tmp_path, monkeypatch)
    token_a = login(client, "masters_user_a", "pass123")
    token_b = login(client, "masters_user_b", "pass123")

    db.add_master_distributor(
        name="WS1 Only Distributor",
        workspace_id="ws-1",
        user_id=_user_id(db, "masters_user_a"),
    )
    db.add_master_distributor(
        name="WS2 Only Distributor",
        workspace_id="ws-2",
        user_id=_user_id(db, "masters_user_b"),
    )

    resp_a = client.get(
        "/api/v1/masters/distributors/export?format=csv",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_a.status_code == 200
    body_a = resp_a.get_data(as_text=True)
    assert "WS1 Only Distributor" in body_a
    assert "WS2 Only Distributor" not in body_a, (
        "BUG: ws-1's export leaked ws-2's distributor data"
    )

    resp_b = client.get(
        "/api/v1/masters/distributors/export?format=csv",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    body_b = resp_b.get_data(as_text=True)
    assert "WS2 Only Distributor" in body_b
    assert "WS1 Only Distributor" not in body_b


def test_export_retailers_distributor_wise_filter(tmp_path, monkeypatch):
    client, db, _db_path = setup_auth_app(tmp_path, monkeypatch)
    token = login(client, "masters_user_a", "pass123")

    user_a_id = _user_id(db, "masters_user_a")
    dist_a = db.add_master_distributor(
        name="Distributor A", workspace_id="ws-1", user_id=user_a_id
    )
    dist_b = db.add_master_distributor(
        name="Distributor B", workspace_id="ws-1", user_id=user_a_id
    )
    db.add_master_retailer(
        name="Retailer Under A",
        distributor_id=dist_a,
        workspace_id="ws-1",
        user_id=user_a_id,
    )
    db.add_master_retailer(
        name="Retailer Under B",
        distributor_id=dist_b,
        workspace_id="ws-1",
        user_id=user_a_id,
    )

    resp = client.get(
        f"/api/v1/masters/retailers/export?format=csv&distributor_id={dist_a}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Retailer Under A" in body
    assert "Retailer Under B" not in body

"""
Verifies the fix for /article-master, which previously crashed on
every request (sqlite3.OperationalError: no such table:
article_master_v2 — the table was queried but never created anywhere
in the codebase). This test confirms:
  1. The route no longer crashes
  2. It's workspace-isolated from day one (bedsheet/textile catalog
     genuinely differs per workspace, per Kunwar's business context)
"""
import importlib

from centralized_db_system.db import CentralizedDB


def setup_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "article_v2_test.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "article-v2-test-key")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply_env()

    app = web_app_module.create_app()
    app.config["TESTING"] = True

    db = CentralizedDB(str(db_path))
    db.create_user("articlev2_user_a", "pass123", role="sales_executive", workspace_id="ws-1")
    db.create_user("articlev2_user_b", "pass123", role="sales_executive", workspace_id="ws-2")

    return app.test_client(), db


def login(client, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]["access_token"]


def test_article_master_route_no_longer_crashes(tmp_path, monkeypatch):
    """Before this fix, this request would raise sqlite3.OperationalError."""
    client, _db = setup_auth_app(tmp_path, monkeypatch)
    token_a = login(client, "articlev2_user_a", "pass123")

    resp = client.get(
        "/article-master?q=Cardinal",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 200, (
        f"Route crashed or errored: {resp.get_data(as_text=True)}"
    )


def test_article_master_isolated_by_workspace(tmp_path, monkeypatch):
    client, db = setup_auth_app(tmp_path, monkeypatch)
    token_a = login(client, "articlev2_user_a", "pass123")

    db.add_article_v2(
        {"brand": "Cardinal", "size": "KS BS", "product": "Bedsheet WS1 Marker"},
        workspace_id="ws-1",
    )
    db.add_article_v2(
        {"brand": "Cardinal", "size": "KS BS", "product": "Bedsheet WS2 Marker"},
        workspace_id="ws-2",
    )

    resp = client.get(
        "/article-master?q=Cardinal",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Bedsheet WS1 Marker" in body
    assert "Bedsheet WS2 Marker" not in body, (
        "ws-1 should not see ws-2's article_master_v2 products"
    )


def test_article_master_requires_auth(tmp_path, monkeypatch):
    client, _db = setup_auth_app(tmp_path, monkeypatch)
    resp = client.get("/article-master?q=test")
    assert resp.status_code in (401, 302)

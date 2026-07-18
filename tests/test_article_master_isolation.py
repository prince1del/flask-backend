"""
Verifies workspace isolation for article_master — confirmed with the
founder that each workspace/executive genuinely sells different
products (e.g. bedsheets/towels vs parlour items), so this must NOT
be shared data (unlike business_rules, which is genuinely global config).
"""
import importlib

from centralized_db_system.db import CentralizedDB


def setup_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / "article_master_test.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "article-master-test-key")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply_env()

    app = web_app_module.create_app()
    app.config["TESTING"] = True

    db = CentralizedDB(str(db_path))
    db.create_user("article_user_a", "pass123", role="sales_executive", workspace_id="ws-1")
    db.create_user("article_user_b", "pass123", role="sales_executive", workspace_id="ws-2")

    return app.test_client(), db


def login(client, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]["access_token"]


def test_articles_page_does_not_show_other_workspace_products(tmp_path, monkeypatch):
    client, db = setup_auth_app(tmp_path, monkeypatch)
    token_a = login(client, "article_user_a", "pass123")

    # Kunwar's example: one workspace sells bedsheets/towels, another sells parlour items
    db.save_article(
        {"category_name": "Bedsheet", "design_name": "Floral WS1", "base_rate": 500},
        workspace_id="ws-1",
    )
    db.save_article(
        {"category_name": "Parlour", "design_name": "Facial Kit WS2", "base_rate": 800},
        workspace_id="ws-2",
    )

    resp = client.get("/articles", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Floral Ws1" in body or "Floral WS1" in body.title() or "floral ws1" in body.lower()
    assert "facial kit ws2" not in body.lower(), (
        "ws-1 (bedsheet seller) should not see ws-2's parlour products"
    )


def test_articles_requires_auth(tmp_path, monkeypatch):
    client, _db = setup_auth_app(tmp_path, monkeypatch)
    resp = client.get("/articles")
    assert resp.status_code in (401, 302)

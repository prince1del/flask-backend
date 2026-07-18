"""Tests for per-user Article Master isolation."""

import importlib
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import article_master_db as amdb


@pytest.fixture
def article_db(tmp_path):
    db_path = tmp_path / "article_user_scope.sqlite3"
    schema_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "article_master_schema.sql",
    )
    conn = sqlite3.connect(db_path)
    with open(schema_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    yield conn, db_path
    conn.close()


def _sample_article(item_key="BRAND|SIZE"):
    return {
        "category": "Bed",
        "product_type": "Bedsheet",
        "brand": "ASTER",
        "size": "DB BS",
        "mrp": 999,
        "ptr": 450,
        "ex_mill_price": 400,
        "bale_pack_size": "10",
        "item_key": item_key,
        "extra_attributes": {"TC": "100"},
    }


def test_users_see_only_their_own_articles(article_db):
    conn, _ = article_db
    amdb.create_category(conn, 1, "Bed", ["brand", "size"], is_confirmed=True, workspace_id="ws-a")
    amdb.create_category(conn, 2, "Bed", ["brand", "size"], is_confirmed=True, workspace_id="ws-b")

    amdb.upsert_article(conn, 1, _sample_article("USER1|KEY"), workspace_id="ws-a")
    amdb.upsert_article(conn, 2, _sample_article("USER2|KEY"), workspace_id="ws-b")

    user1_articles = amdb.get_all_articles(conn, 1)
    user2_articles = amdb.get_all_articles(conn, 2)

    assert len(user1_articles) == 1
    assert len(user2_articles) == 1
    assert user1_articles[0]["item_key"] == "USER1|KEY"
    assert user2_articles[0]["item_key"] == "USER2|KEY"


def test_user_cannot_edit_another_users_article(article_db):
    conn, _ = article_db
    amdb.create_category(conn, 1, "Bed", ["brand", "size"], is_confirmed=True)
    article, _, _ = amdb.upsert_article(conn, 1, _sample_article())

    with pytest.raises(ValueError, match="Article not found"):
        amdb.manual_edit_article(conn, 2, article["id"], "mrp", 1200)


def test_any_user_can_edit_own_article(article_db):
    conn, _ = article_db
    amdb.create_category(conn, 5, "Bed", ["brand", "size"], is_confirmed=True)
    article, _, _ = amdb.upsert_article(conn, 5, _sample_article(), changed_by="upload")

    updated = amdb.manual_edit_article(conn, 5, article["id"], "mrp", 1100, changed_by="sales_exec")
    assert updated["mrp"] == 1100

    history = amdb.get_price_history(conn, article["id"], user_id=5)
    assert len(history) == 1
    assert history[0]["changed_by"] == "sales_exec"


def test_article_master_api_requires_auth(tmp_path, monkeypatch):
    db_path = tmp_path / "api_article.sqlite3"
    schema_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "article_master_schema.sql",
    )
    conn = sqlite3.connect(db_path)
    with open(schema_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.close()

    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "article-master-test-key")

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)

    app = web_app_module.create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    resp = client.get("/api/v1/article-master/list")
    assert resp.status_code in (401, 302)

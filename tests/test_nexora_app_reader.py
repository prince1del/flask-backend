"""Ask Nexora read_app: allow-list, user isolation through the app's own routes, trimming."""

from __future__ import annotations

import importlib

from app.services import nexora_app_reader as r


def test_allow_list():
    assert r.allowed("/api/v1/masters/distributors") == "/api/v1/masters/distributors"
    assert r.allowed("/api/v1/order-fulfillment/tracking/116") == "/api/v1/order-fulfillment/tracking/{id}"
    assert r.allowed("/api/v1/pjp/months/2026-11") == "/api/v1/pjp/months/{year_month}"
    assert r.allowed("/api/v1/order-fulfillment/tracking/116/file") is None  # PDFs are not for the AI
    assert r.allowed("/api/v1/masters/distributors/export") is None
    assert r.allowed("/api/v1/admin/users") is None
    assert r.allowed("/api/v1/masters/../admin") is None
    assert r.allowed("https://evil.example/api/v1/search") is None


def test_trim_keeps_replies_small():
    big = {"items": [{"n": i, "text": "x" * 1000} for i in range(100)], "raw": "y" * 9000}
    out = r.trim(big)
    assert "raw" not in out
    assert len(out["items"]) == 41 and "60 more" in out["items"][-1]
    assert out["items"][0]["text"].endswith("…") and len(out["items"][0]["text"]) == 401


def _app(tmp_path, monkeypatch):
    db_path = tmp_path / "reader.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "reader-test-key-0123456789abcdef")
    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    app = web_app_module.create_app()
    app.config["TESTING"] = True
    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(str(db_path))
    db.create_user("reader_a", "pass123", role="sales_executive", workspace_id="ws-a")
    db.create_user("reader_b", "pass123", role="sales_executive", workspace_id="ws-b")
    return app


def test_read_app_runs_as_the_asking_user(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    client = app.test_client()
    tok_a = client.post("/api/v1/auth/login", json={"username": "reader_a", "password": "pass123"}).get_json()["data"]["access_token"]
    tok_b = client.post("/api/v1/auth/login", json={"username": "reader_b", "password": "pass123"}).get_json()["data"]["access_token"]
    # User A adds a to-do through the normal route.
    client.post("/api/v1/personal-todos", json={"task_title": "Call Kalra about payment"},
                headers={"Authorization": f"Bearer {tok_a}"})

    def read_as(token):
        with app.test_request_context("/api/v1/ai-assistant/query", headers={"Authorization": f"Bearer {token}"}):
            return r.read_app("/api/v1/personal-todos", None, user_id=0, workspace_id="x")

    text_a = str(read_as(tok_a))
    text_b = str(read_as(tok_b))
    assert "Call Kalra about payment" in text_a
    assert "Call Kalra about payment" not in text_b  # B never sees A's data

    with app.test_request_context("/x", headers={"Authorization": f"Bearer {tok_a}"}):
        assert "error" in r.read_app("/api/v1/order-fulfillment/tracking/1/file", None, user_id=0, workspace_id="x")


def test_catalog_lists_query_params(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    text = r.catalog_text(app)
    assert "/api/v1/search - Search everything" in text
    assert "[query:" in text

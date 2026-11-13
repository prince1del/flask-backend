"""Galaxy Watch: pairing key, summary, ask; key cannot open other routes; revoke."""

from __future__ import annotations

import importlib


def _app(tmp_path, monkeypatch):
    db_path = tmp_path / "watch.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "watch-test-key-0123456789abcdefgh")
    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    app = web_app_module.create_app()
    app.config["TESTING"] = True
    from centralized_db_system.db import CentralizedDB

    CentralizedDB(str(db_path)).create_user("watch_user", "pass123", role="sales_executive", workspace_id="ws-w")
    client = app.test_client()
    tok = client.post("/api/v1/auth/login", json={"username": "watch_user", "password": "pass123"}).get_json()["data"]["access_token"]
    return app, client, {"Authorization": f"Bearer {tok}"}


def test_pair_summary_ask_revoke(tmp_path, monkeypatch):
    app, client, h = _app(tmp_path, monkeypatch)
    assert client.post("/api/v1/watch/pair").status_code == 401  # needs the phone login

    key = client.post("/api/v1/watch/pair", headers=h).get_json()["data"]["watch_key"]
    assert key.startswith("nxw_")
    w = {"X-Watch-Key": key}

    s = client.get("/api/v1/watch/summary", headers=w)
    assert s.status_code == 200, s.get_data(as_text=True)
    data = s.get_json()["data"]
    for k in ("season", "to_collect", "pending_so_count", "overdue_count", "todos_due", "so_mails_waiting"):
        assert k in data

    assert client.get("/api/v1/watch/summary", headers={"X-Watch-Key": "nxw_wrong"}).status_code == 401
    assert client.get("/api/v1/watch/summary").status_code == 401
    # The watch key opens nothing else.
    assert client.get("/api/v1/personal-todos", headers=w).status_code == 401

    from app.services import nexora_ai_agent as a

    monkeypatch.setattr(a, "ask_order_desk", lambda q, **k: "Kalra ka koi payment baaki nahi.")
    r = client.post("/api/v1/watch/ask", json={"question": "Kalra ka kitna baaki"}, headers=w)
    assert r.status_code == 200 and r.get_json()["data"]["answer"].startswith("Kalra")
    assert client.post("/api/v1/watch/ask", json={}, headers=w).status_code == 400

    assert client.delete("/api/v1/watch/pair", headers=h).get_json()["data"]["revoked"] == 1
    assert client.get("/api/v1/watch/summary", headers=w).status_code == 401

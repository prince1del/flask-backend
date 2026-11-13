"""Ask Nexora actions: proposed by the AI, saved only after the same user confirms."""

from __future__ import annotations

import importlib
import json

from app.services import nexora_actions as na


def test_build_request_checks_fields_and_ids():
    assert na.build_request("add_todo", {"task_title": "Call Kalra"}) == (
        "POST", "/api/v1/personal-todos", {"task_title": "Call Kalra"})
    assert na.build_request("todo_done", {"todo_id": 7}) == ("POST", "/api/v1/personal-todos/7/done", {})
    assert "Missing" in na.build_request("add_deposit", {"distributor_id": 1})
    assert "number" in na.build_request("todo_done", {"todo_id": "7/../../admin"})
    assert "YYYY-MM-DD" in na.build_request("set_pjp_day", {"plan_date": "kal"})
    assert "Unknown action" in na.build_request("delete_everything", {})


def test_reply_kind():
    assert na.reply_kind("Haan") == "yes" and na.reply_kind("haan kar do!") == "yes"
    assert na.reply_kind("nahi") == "no" and na.reply_kind("cancel") == "no"
    assert na.reply_kind("haan Kalra ka payment kitna hai") is None


def _app(tmp_path, monkeypatch):
    db_path = tmp_path / "actions.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "actions-test-key-0123456789abcdef")
    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    app = web_app_module.create_app()
    app.config["TESTING"] = True
    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(str(db_path))
    db.create_user("act_a", "pass123", role="sales_executive", workspace_id="ws-a")
    db.create_user("act_b", "pass123", role="sales_executive", workspace_id="ws-a")
    client = app.test_client()
    toks = {u: client.post("/api/v1/auth/login", json={"username": u, "password": "pass123"}).get_json()["data"]["access_token"]
            for u in ("act_a", "act_b")}
    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        ids = {u: conn.execute("SELECT id FROM users WHERE username=?", (u,)).fetchone()[0] for u in toks}
    return app, client, str(db_path), toks, ids


def _todos(client, tok):
    return str(client.get("/api/v1/personal-todos", headers={"Authorization": f"Bearer {tok}"}).get_json())


def test_nothing_saved_until_the_same_user_confirms(tmp_path, monkeypatch):
    app, client, db_path, toks, ids = _app(tmp_path, monkeypatch)
    h_a = {"Authorization": f"Bearer {toks['act_a']}"}
    h_b = {"Authorization": f"Bearer {toks['act_b']}"}

    out = na.propose(db_path, user_id=ids["act_a"], workspace_id="ws-a", action="add_todo",
                     params={"task_title": "Follow up Savitri payment"}, summary="To-Do: Follow up Savitri payment")
    aid = out["action_id"]
    assert "Follow up Savitri payment" not in _todos(client, toks["act_a"])  # proposing saves nothing

    pend = client.get("/api/v1/ai/actions/pending", headers=h_a).get_json()["data"]["action"]
    assert pend["id"] == aid
    assert client.get("/api/v1/ai/actions/pending", headers=h_b).get_json()["data"]["action"] is None

    # Another user cannot confirm it.
    other = client.post(f"/api/v1/ai/actions/{aid}/confirm", headers=h_b).get_json()
    assert other["success"] is False
    assert "Follow up Savitri payment" not in _todos(client, toks["act_a"])

    done = client.post(f"/api/v1/ai/actions/{aid}/confirm", headers=h_a).get_json()
    assert done["success"] is True, done
    assert "Follow up Savitri payment" in _todos(client, toks["act_a"])  # saved for A
    assert "Follow up Savitri payment" not in _todos(client, toks["act_b"])

    again = client.post(f"/api/v1/ai/actions/{aid}/confirm", headers=h_a).get_json()
    assert again["success"] is False  # a double tap does not save twice
    assert _todos(client, toks["act_a"]).count("Follow up Savitri payment") == 1


def test_cancel_and_haan_by_chat(tmp_path, monkeypatch):
    app, client, db_path, toks, ids = _app(tmp_path, monkeypatch)
    h_a = {"Authorization": f"Bearer {toks['act_a']}"}
    aid = na.propose(db_path, user_id=ids["act_a"], workspace_id="ws-a", action="add_todo",
                     params={"task_title": "Visit Goyal"}, summary="To-Do: Visit Goyal")["action_id"]
    assert client.post(f"/api/v1/ai/actions/{aid}/cancel", headers=h_a).get_json()["success"] is True
    assert "Visit Goyal" not in _todos(client, toks["act_a"])

    na.propose(db_path, user_id=ids["act_a"], workspace_id="ws-a", action="add_todo",
               params={"task_title": "Call DCA"}, summary="To-Do: Call DCA")
    r = client.post("/api/v1/ai-assistant/query", json={"query": "haan"}, headers=h_a).get_json()
    assert r["intent"] == "ai_action" and r["answer"].startswith("Done")
    assert "Call DCA" in _todos(client, toks["act_a"])


def test_ai_proposal_comes_back_with_the_answer(tmp_path, monkeypatch):
    app, client, db_path, toks, ids = _app(tmp_path, monkeypatch)
    from app.services import nexora_ai_agent as a

    monkeypatch.setattr(a, "_log_usage", lambda **k: None)
    monkeypatch.setattr(a, "over_daily_limit", lambda *x: False)
    calls = []

    def fake_call(contents):
        calls.append(contents)
        if len(calls) == 1:
            return {"candidates": [{"content": {"parts": [{"functionCall": {"name": "propose_action", "args": {
                "action": "add_todo", "params": {"task_title": "Send catalogue to Kalra"},
                "summary": "To-Do: Send catalogue to Kalra"}}}]}}]}, "m"
        return {"candidates": [{"content": {"parts": [{"text": "Yeh To-Do bana doon? Confirm dabaiye."}]}}]}, "m"

    monkeypatch.setattr(a, "_call_gemini", fake_call)
    r = client.post("/api/v1/ai-assistant/query", json={"query": "Kalra ko catalogue bhejna hai, to-do bana do"},
                    headers={"Authorization": f"Bearer {toks['act_a']}"}).get_json()
    assert r["action"]["summary"] == "To-Do: Send catalogue to Kalra"
    assert "Send catalogue to Kalra" not in _todos(client, toks["act_a"])
    # A pattern containing a change is never learned for replay.
    assert a.learn_template("Kalra ko catalogue bhejna hai", [{"name": "propose_action", "args": {}}]) is None

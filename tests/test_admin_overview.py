"""Admin Zone home numbers: founder sees all companies + health; an owner sees only their own workspace."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from app.services import admin_overview as ao, subscriptions


def _db(tmp_path):
    p = str(tmp_path / "ao.sqlite3")
    now = datetime.now(timezone.utc)
    with sqlite3.connect(p) as c:
        c.executescript("""
            CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, workspace_id TEXT, active_session_at TEXT,
                                trial_ends_at TEXT, paid_until TEXT, status TEXT);
            CREATE TABLE login_approvals (id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT);
            CREATE TABLE ai_agent_usage_log (id INTEGER PRIMARY KEY, created_at TEXT, user_id INTEGER, total_tokens INTEGER);
        """)
        c.executemany("INSERT INTO companies (name) VALUES (?)", [("A",), ("B",)])
        c.executemany("INSERT INTO users (username, workspace_id, active_session_at, trial_ends_at, status) VALUES (?,?,?,?,?)", [
            ("owner_a", "ws_a", now.isoformat(), None, "active"),
            ("rep_a", "ws_a", None, (now + timedelta(days=3)).isoformat(), "active"),
            ("owner_b", "ws_b", now.isoformat(), None, "blocked"),
        ])
        c.execute("INSERT INTO login_approvals (user_id, status) VALUES (2, 'pending')")
        c.execute("INSERT INTO ai_agent_usage_log (created_at, user_id, total_tokens) VALUES (?, 1, 500)",
                  (now.strftime("%Y-%m-%d %H:%M:%S"),))
    return p


def test_owner_sees_only_own_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(subscriptions, "is_super_admin", lambda u: False)
    d = ao.overview(_db(tmp_path), {"workspace_id": "ws_a", "is_workspace_owner": True})
    assert d["scope"] == "owner" and d["logins"] == 2 and d["active_today"] == 1
    assert d["trials_ending"][0]["login"] == "rep_a" and d["sign_in_waiting"] == 1 and d["ai_calls_today"] == 1
    assert "health" not in d and "companies" not in d


def test_founder_sees_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(subscriptions, "is_super_admin", lambda u: True)
    d = ao.overview(_db(tmp_path), {"workspace_id": "ws_a"})
    assert (d["companies"], d["logins"], d["active_today"], d["switched_off"]) == (2, 3, 2, 1)
    assert d["ai_tokens_today"] == 500 and "database_mb" in d["health"]

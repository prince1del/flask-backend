"""One phone + one computer per login (owner 21-11); the founder is free; the founder can gift more devices;
the oldest device of a kind gives way."""
from __future__ import annotations

import sqlite3

from centralized_db_system.db import CentralizedDB
from app.jwt_service import session_is_current


def _db(tmp_path, monkeypatch):
    path = str(tmp_path / "dev.sqlite3")
    db = CentralizedDB(path)
    db.ensure_user_profile_columns()
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO users (username, password_hash, role, workspace_id, created_at) "
                     "VALUES ('shop1', 'x', 'admin', 'ws1', '2026-11-21')")
        uid = conn.execute("SELECT id FROM users WHERE username='shop1'").fetchone()[0]
    return db, uid


def test_slots_and_allowance(tmp_path, monkeypatch):
    db, uid = _db(tmp_path, monkeypatch)
    db.set_active_session(uid, "p1", "Fold", slot="phone")
    db.set_active_session(uid, "c1", "Chrome", slot="computer")
    assert set(db.get_slot_sessions(uid).values()) == {"p1", "c1"}       # a phone and a computer together
    db.set_active_session(uid, "p2", "Vivo", slot="phone")
    assert set(db.get_slot_sessions(uid).values()) == {"p2", "c1"}       # the second phone ends the first
    db.set_device_allowance(uid, 2, 1)                                   # founder gifts a second phone
    db.set_active_session(uid, "p3", "Tab", slot="phone")
    assert set(db.get_slot_sessions(uid).values()) == {"p2", "p3", "c1"}
    db.set_active_session(uid, "p4", "New", slot="phone")                # oldest phone (p2) gives way
    assert set(db.get_slot_sessions(uid).values()) == {"p3", "p4", "c1"}
    db.set_device_allowance(uid, 1, 1)                                   # back to one phone: the newest stays
    assert set(db.get_slot_sessions(uid).values()) == {"p4", "c1"}
    db.clear_active_session(uid, "c1")
    assert set(db.get_slot_sessions(uid).values()) == {"p4"}


def test_session_check(tmp_path, monkeypatch):
    from flask import Flask

    db, uid = _db(tmp_path, monkeypatch)
    app = Flask(__name__)
    app.config["DATABASE_PATH"] = db.db_path
    monkeypatch.setenv("WORKSPACE_OWNER_USERNAME", "founder1")
    db.set_active_session(uid, "p1", "Fold", slot="phone")
    with app.app_context():
        assert session_is_current({"user_id": uid, "username": "shop1", "sid": "p1"})
        assert not session_is_current({"user_id": uid, "username": "shop1", "sid": "old"})
        assert not session_is_current({"user_id": uid, "username": "shop1"})          # no sid once a session is live
        # an owner flag alone no longer frees a login; only the founder is free
        assert not session_is_current({"user_id": uid, "username": "shop1", "sid": "old", "is_workspace_owner": True})
        assert session_is_current({"user_id": uid, "username": "founder1", "sid": "anything"})

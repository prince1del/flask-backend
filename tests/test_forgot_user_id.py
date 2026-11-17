"""Forgot user ID: email / mobile → secret question → user id; 5 wrong answers lock it for an hour."""
from __future__ import annotations

from centralized_db_system.db import CentralizedDB


def _db(tmp_path):
    db = CentralizedDB(str(tmp_path / "rec.sqlite3"))
    db.create_user("ravi_k", "StrongPass123")
    uid = next(p["id"] for p in (db.get_user_profile(i) for i in range(1, 5)) if p and p["username"] == "ravi_k")
    db.update_user_profile(uid, email="ravi@example.com", phone="+91 98765 43210")
    db.set_secret_question(uid, db.SECRET_QUESTIONS[0], "Jaipur")
    return db


def test_find_by_email_or_mobile(tmp_path):
    db = _db(tmp_path)
    assert db.user_id_recovery_question("ravi@example.com") == db.SECRET_QUESTIONS[0]
    assert db.user_id_recovery_question("9876543210") == db.SECRET_QUESTIONS[0]
    assert db.user_id_recovery_question("1111111111") is None
    assert db.recover_user_id("9876543210", "  jaipur ") == (True, "Found your account", "ravi_k")
    assert db.user_id_and_email("98765 43210") == ("ravi_k", "ravi@example.com")


def test_five_wrong_answers_lock(tmp_path):
    db = _db(tmp_path)
    for _ in range(5):
        ok, _msg, uid = db.recover_user_id("ravi@example.com", "Delhi")
        assert not ok and uid is None
    ok, msg, _ = db.recover_user_id("ravi@example.com", "Jaipur")
    assert not ok and "Too many" in msg

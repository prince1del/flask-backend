"""Sign-up email code: made, checked, wrong tries end it, resend waits a minute, expiry."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.services import signup_email_code as sec


def _db(tmp_path):
    p = str(tmp_path / "su.sqlite3")
    sqlite3.connect(p).execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)").connection.commit()
    return p


def test_code_round_trip_and_wrong_tries(tmp_path):
    p = _db(tmp_path)
    code = sec.new_code(p, "New@Shop.in")
    assert len(code) == 6 and code.isdigit()
    assert sec.check_code(p, "new@shop.in", "000000" if code != "000000" else "111111")[0] is False
    assert sec.check_code(p, "new@shop.in", code, consume=False) == (True, "ok")
    for _ in range(5):
        sec.check_code(p, "new@shop.in", "999999" if code != "999999" else "888888")
    assert "Too many" in sec.check_code(p, "new@shop.in", code)[1]


def test_resend_waits_and_expiry(tmp_path):
    p = _db(tmp_path)
    sec.new_code(p, "a@b.in")
    with pytest.raises(ValueError):
        sec.new_code(p, "a@b.in")  # within 60 seconds
    old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    with sqlite3.connect(p) as c:
        c.execute("UPDATE signup_email_codes SET sent_at=?, expires_at=?", (old, old))
    code = sec.new_code(p, "a@b.in")  # allowed again
    with sqlite3.connect(p) as c:
        c.execute("UPDATE signup_email_codes SET expires_at=?", (old,))
    assert "expired" in sec.check_code(p, "a@b.in", code)[1]


def test_bad_email_and_taken(tmp_path):
    p = _db(tmp_path)
    with pytest.raises(ValueError):
        sec.new_code(p, "not-an-email")
    with sqlite3.connect(p) as c:
        c.execute("INSERT INTO users (email) VALUES ('owner@shop.in')")
    assert sec.email_taken(p, " OWNER@shop.in ") is True
    assert sec.email_taken(p, "other@shop.in") is False


def test_send_code_only_for_a_complete_email():
    from app.services.signup_email_code import is_complete_email
    assert is_complete_email("name@gmail.com")
    assert is_complete_email("a.b+c@mail.example.co.in")
    assert is_complete_email("me@yahoo.co.in") and is_complete_email("ca@sharmafirm.co")
    for half in ("kpsj.bd@", "kpsj.bd@gmail", "kpsj.bd@gmail.", "kpsj.bd@gmail.c", "kpsj.bd@gmail.co",
                 "a@yahoo.co", "a@outlook.co", "a@rediffmail.co", "@gmail.com", "kp sj@gmail.com", ""):
        assert not is_complete_email(half), half

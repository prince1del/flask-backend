"""Sign-up email check: a 6-digit code goes to the email typed on the sign-up form; the account is made
only with the right code. Codes are kept hashed, last 15 minutes, can be re-sent after 60 seconds
(max 5 an hour), and 5 wrong tries end the code."""
from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from werkzeug.security import check_password_hash, generate_password_hash

CODE_MINUTES = 15
RESEND_SECONDS = 60
MAX_PER_HOUR = 5
MAX_TRIES = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS signup_email_codes (
               email TEXT PRIMARY KEY,
               code_hash TEXT NOT NULL,
               expires_at TEXT NOT NULL,
               tries INTEGER NOT NULL DEFAULT 0,
               sent_at TEXT NOT NULL,
               sends_this_hour INTEGER NOT NULL DEFAULT 1,
               hour_started TEXT NOT NULL
           )""")


def _norm(email: str) -> str:
    return (email or "").strip().lower()


_EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$", re.I)


# Big mail providers: the domain must be typed in full — "gmail.co" / "gmail.c" is still being typed (owner, 20-11).
MAIL_PROVIDERS = {
    "gmail": {"gmail.com"}, "googlemail": {"googlemail.com"},
    "yahoo": {"yahoo.com", "yahoo.co.in", "yahoo.in", "yahoo.co.uk"}, "ymail": {"ymail.com"},
    "hotmail": {"hotmail.com", "hotmail.co.in", "hotmail.co.uk"}, "outlook": {"outlook.com", "outlook.in"},
    "live": {"live.com", "live.in"}, "msn": {"msn.com"}, "icloud": {"icloud.com"}, "me": {"me.com"},
    "rediffmail": {"rediffmail.com"}, "rediff": {"rediff.com"}, "protonmail": {"protonmail.com"},
    "proton": {"proton.me"}, "zoho": {"zoho.com", "zoho.in"}, "zohomail": {"zohomail.in", "zohomail.com"},
    "aol": {"aol.com"},
}


def is_complete_email(email: str) -> bool:
    """name@domain.tld — "kpsj.bd@", "a@gmail", "a@gmail.co" are not finished yet (same rule as phone + web form)."""
    e = (email or "").strip().lower()
    if not _EMAIL_RE.match(e):
        return False
    domain = e.rsplit("@", 1)[1]
    allowed = MAIL_PROVIDERS.get(domain.split(".", 1)[0])
    return domain in allowed if allowed else True


def email_taken(db_path: str, email: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        try:
            return conn.execute("SELECT 1 FROM users WHERE lower(trim(email)) = ?", (_norm(email),)).fetchone() is not None
        except sqlite3.OperationalError:
            return False


def new_code(db_path: str, email: str) -> str:
    """Make (or replace) the code for this email. Raises ValueError when asked again too soon / too often."""
    e = _norm(email)
    if not is_complete_email(e):
        raise ValueError("Enter your full email (like name@gmail.com)")
    now = _now()
    with sqlite3.connect(db_path) as conn:
        _ensure(conn)
        row = conn.execute("SELECT sent_at, sends_this_hour, hour_started FROM signup_email_codes WHERE email = ?",
                           (e,)).fetchone()
        sends, hour_started = 1, now
        if row:
            if now - datetime.fromisoformat(row[0]) < timedelta(seconds=RESEND_SECONDS):
                raise ValueError("Please wait a minute before asking for a new code")
            started = datetime.fromisoformat(row[2])
            if now - started < timedelta(hours=1):
                if int(row[1]) >= MAX_PER_HOUR:
                    raise ValueError("Too many codes for this email — try again in an hour")
                sends, hour_started = int(row[1]) + 1, started
        code = f"{secrets.randbelow(1_000_000):06d}"
        conn.execute(
            "INSERT OR REPLACE INTO signup_email_codes (email, code_hash, expires_at, tries, sent_at, sends_this_hour, hour_started) "
            "VALUES (?, ?, ?, 0, ?, ?, ?)",
            (e, generate_password_hash(code), (now + timedelta(minutes=CODE_MINUTES)).isoformat(), now.isoformat(),
             sends, hour_started.isoformat()))
        conn.commit()
    return code


def check_code(db_path: str, email: str, code: str, consume: bool = True) -> tuple[bool, str]:
    e, c = _norm(email), "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(c) != 6:
        return False, "Enter the 6-digit code from your email"
    with sqlite3.connect(db_path) as conn:
        _ensure(conn)
        row = conn.execute("SELECT code_hash, expires_at, tries FROM signup_email_codes WHERE email = ?", (e,)).fetchone()
        if not row:
            return False, "Tap Send code first"
        if int(row[2]) >= MAX_TRIES:
            return False, "Too many wrong codes — tap Send code for a new one"
        if _now() > datetime.fromisoformat(row[1]):
            return False, "This code has expired — tap Send code for a new one"
        if not check_password_hash(row[0], c):
            conn.execute("UPDATE signup_email_codes SET tries = tries + 1 WHERE email = ?", (e,))
            conn.commit()
            return False, "Wrong code — check your email"
        if consume:
            conn.execute("DELETE FROM signup_email_codes WHERE email = ?", (e,))
            conn.commit()
    return True, "ok"

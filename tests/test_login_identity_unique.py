"""Strict rule: one email = one login account."""

from __future__ import annotations

import os
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from centralized_db_system.db import CentralizedDB


@pytest.fixture()
def db(tmp_path):
    os.environ.setdefault("SECRET_KEY", "test-login-identity")
    path = tmp_path / "login_identity.sqlite3"
    return CentralizedDB(str(path))


def test_email_username_cannot_be_two_accounts(db: CentralizedDB):
    db.create_user(
        "owner@example.com",
        "Secret123!",
        role="sales_executive",
        workspace_id="ws-a",
    )
    with pytest.raises(ValueError, match="One email = one account"):
        db.create_user(
            "owner@example.com",
            "Other456!",
            role="distributor",
            workspace_id="ws-b",
        )


def test_username_cannot_collide_with_existing_email(db: CentralizedDB):
    db.create_user(
        "alice",
        "Secret123!",
        role="sales_executive",
        workspace_id="ws-a",
        email="shared@example.com",
    )
    with pytest.raises(ValueError, match="One email = one account"):
        db.create_user(
            "shared@example.com",
            "Other456!",
            role="retailer",
            workspace_id="ws-b",
        )


def test_email_update_blocked_when_taken(db: CentralizedDB):
    a = db.create_user("alice", "Secret123!", role="sales_executive", workspace_id="ws-a")
    db.create_user(
        "bob@example.com",
        "Secret123!",
        role="distributor",
        workspace_id="ws-b",
        email="bob@example.com",
    )
    with pytest.raises(ValueError, match="One email = one account"):
        db.update_user_profile(int(a["id"]), email="bob@example.com")


def test_login_resolves_username_or_email_to_same_account(db: CentralizedDB):
    db.create_user(
        "kunwar1del",
        "Secret123!",
        role="sales_executive",
        workspace_id="ws-a",
        email="kps.julka@gmail.com",
    )
    assert db.authenticate_user("kunwar1del", "Secret123!")
    assert db.authenticate_user("kps.julka@gmail.com", "Secret123!")
    assert not db.authenticate_user("kps.julka@gmail.com", "wrong")


def test_dedupe_archives_duplicate_email_login(db: CentralizedDB):
    owner = db.create_user(
        "kunwar1del",
        "Secret123!",
        role="sales_executive",
        workspace_id="ws-a",
        email="kps.julka@gmail.com",
    )
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE users SET is_workspace_owner = 1 WHERE id = ?",
            (int(owner["id"]),),
        )
        conn.execute("DROP INDEX IF EXISTS idx_users_email_lower")
        conn.execute(
            """
            INSERT INTO users (username, password_hash, created_at, role, workspace_id, status, email)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "kps.julka@gmail.com",
                generate_password_hash("legacy"),
                "2026-01-01T00:00:00+00:00",
                "sales_executive",
                "ws-b",
                "active",
                "kps.julka@gmail.com",
            ),
        )
        conn.commit()

    result = db.dedupe_email_login_accounts(prefer_username="kunwar1del")
    assert result["changes"]

    with sqlite3.connect(db.db_path) as conn:
        ghost = conn.execute(
            "SELECT id FROM users WHERE lower(username) LIKE 'archived_dup_%'"
        ).fetchone()
        assert ghost is None

    profile = db.update_user_profile(
        int(owner["id"]),
        username="kunwar1del",
        email="kps.julka@gmail.com",
        full_name="Kunwar",
    )
    assert profile["email"] == "kps.julka@gmail.com"

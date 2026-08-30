"""GET /api/v1/order-fulfillment/order-match/list — smoke + purge safety."""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest

from app.services import fo_so_match_db as matchdb
from app.services import order_desk_archive as oda


def _setup_app(tmp_path, monkeypatch):
    db_path = tmp_path / "order_match_api.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "order-match-list-test-key")

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)

    app = web_app_module.create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(str(db_path))
    db.create_user("om_user", "pass123", role="sales_executive", workspace_id="ws-1")

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "om_user", "password": "pass123"},
    )
    assert login.status_code == 200, login.get_data(as_text=True)
    token = login.get_json()["data"]["access_token"]
    return client, token, db_path


def test_order_match_list_empty(tmp_path, monkeypatch):
    client, token, _ = _setup_app(tmp_path, monkeypatch)
    resp = client.get(
        "/api/v1/order-fulfillment/order-match/list",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["runs"] == []
    assert body["data"]["count"] == 0


def test_order_match_list_survives_expired_archive_purge(tmp_path, monkeypatch):
    client, token, db_path = _setup_app(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(db_path))
    matchdb.ensure_schema(conn)
    oda.ensure_schema(conn)
    conn.execute(
        """
        INSERT INTO order_desk_archive (
            user_id, kind, entity_key, restore_scope, payload_json,
            created_at, expires_at
        ) VALUES (1, 'file', 'k', 'run', ?, '2020-01-01', '2020-01-02')
        """,
        ('{"recycle_path": "missing.bin"}',),
    )
    conn.commit()
    conn.close()

    oda._last_purge_at = 0.0
    resp = client.get(
        "/api/v1/order-fulfillment/order-match/list",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["success"] is True

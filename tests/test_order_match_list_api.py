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


def test_delete_single_so_survives_archive_failure(tmp_path, monkeypatch):
    """Recycle archive must not block SO delete (same as replace/split)."""
    client, token, db_path = _setup_app(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(db_path))
    matchdb.ensure_schema(conn)
    user_row = conn.execute("SELECT id FROM users WHERE username = 'om_user'").fetchone()
    uid = int(user_row[0])
    payload = {
        "fo": {"id": 1},
        "match": {
            "totals": {"fo_qty": 10, "so_qty": 10},
            "counts": {"MATCH": 1},
            "rows": [
                {
                    "status": "MATCH",
                    "fo_qty": 10,
                    "so_qty": 10,
                    "so_numbers": ["555444333"],
                    "so_breakdown": [
                        {"so_number": "555444333", "qty": 10, "net": 100}
                    ],
                }
            ],
        },
    }
    pack = {"line_detail": [{"so_number": "555444333", "qty": 10, "net_amount": 100}]}
    run = matchdb.save_match_run(conn, user_id=uid, match_payload=payload, so_pack=pack)
    run_id = int(run["id"])
    conn.commit()
    conn.close()

    import app.services.order_desk_archive as oda

    def _boom(*_a, **_k):
        raise RuntimeError("archive down")

    monkeypatch.setattr(oda, "archive_match_so", _boom)

    resp = client.delete(
        f"/api/v1/order-fulfillment/order-match/{run_id}?so_number=555444333",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["deleted"] is True

    conn = sqlite3.connect(str(db_path))
    assert matchdb.get_match_run(conn, run_id, user_id=uid) is None
    conn.close()

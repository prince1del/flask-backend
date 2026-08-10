"""HoP wipe-data must verify the signed-in user's password before deleting."""

from __future__ import annotations

import importlib
import os

os.environ.setdefault("SECRET_KEY", "test-wipe-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-wipe-secret")
os.environ["AUTH_ENABLED"] = "true"

from centralized_db_system.db import CentralizedDB


def _setup_hop_wipe_client(tmp_path, monkeypatch):
    db_path = tmp_path / "wipe_test.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "test-wipe-secret")
        monkeypatch.setenv("JWT_SECRET_KEY", "test-wipe-secret")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply_env()

    # create_app first so article_master schema is the user_id-scoped one
    # (CentralizedDB alone would create the legacy article_master shape).
    app = web_app_module.create_app()
    app.config.update(TESTING=True, DATABASE_PATH=str(db_path))

    db = CentralizedDB(str(db_path))
    db.create_user("hopwipe", "correct-horse", role="hop_admin", workspace_id="default")

    client = app.test_client()
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "hopwipe", "password": "correct-horse"},
    )
    assert login.status_code == 200, login.get_data(as_text=True)
    token = login.get_json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    return client, headers, str(db_path)


def test_wipe_rejects_missing_password(tmp_path, monkeypatch):
    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    resp = client.post(
        "/api/v1/hop/settings/wipe-data",
        headers=headers,
        json={"confirm": "WIPE"},
    )
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["success"] is False
    assert body["error"]["code"] == "PASSWORD_REQUIRED"


def test_wipe_rejects_wrong_password(tmp_path, monkeypatch):
    client, headers, db_path = _setup_hop_wipe_client(tmp_path, monkeypatch)
    from app import hop_db
    from app.hop_schema import HOP_WORKSPACE_ID, ensure_hop_schema

    ensure_hop_schema(db_path)
    with hop_db.connect(db_path) as conn:
        hop_db.create_customer(
            conn,
            HOP_WORKSPACE_ID,
            {"company": "Keep Me"},
        )

    resp = client.post(
        "/api/v1/hop/settings/wipe-data",
        headers=headers,
        json={"password": "wrong-password", "confirm": "WIPE"},
    )
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_PASSWORD"

    with hop_db.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM hop_customers WHERE workspace_id = ?",
            (HOP_WORKSPACE_ID,),
        ).fetchone()[0]
    assert int(count) >= 1


def test_wipe_accepts_correct_password(tmp_path, monkeypatch):
    client, headers, db_path = _setup_hop_wipe_client(tmp_path, monkeypatch)
    from app import hop_db
    from app.hop_schema import HOP_WORKSPACE_ID, ensure_hop_schema

    ensure_hop_schema(db_path)
    with hop_db.connect(db_path) as conn:
        hop_db.create_customer(
            conn,
            HOP_WORKSPACE_ID,
            {"company": "Wipe Me"},
        )

    resp = client.post(
        "/api/v1/hop/settings/wipe-data",
        headers=headers,
        json={"password": "correct-horse", "confirm": "WIPE"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True

    with hop_db.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM hop_customers WHERE workspace_id = ?",
            (HOP_WORKSPACE_ID,),
        ).fetchone()[0]
    assert int(count) == 0

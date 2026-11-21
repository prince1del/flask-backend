"""One live device per account (owner exempt, or owner-approved exception).

Logging in on a second phone/desktop must invalidate the first device's tokens.
"""

from __future__ import annotations

import pytest
from app import create_app
from centralized_db_system.db import CentralizedDB


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "single_session.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "test-secret-single-session-32b")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("WORKSPACE_OWNER_USERNAME", "kunwar1del")

    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


def _cdb(app) -> CentralizedDB:
    return CentralizedDB(app.config["DATABASE_PATH"])


def _login(client, username, password="pass1234", device=None):
    payload = {"username": username, "password": password}
    if device:
        payload["device_label"] = device
    res = client.post("/api/v1/auth/login", json=payload)
    assert res.status_code == 200, res.get_json()
    return res.get_json()["data"]


def _probe(client, access_token):
    """Any JWT-protected route; only the auth outcome matters here."""
    return client.get(
        "/api/v1/me/profile",
        headers={"Authorization": f"Bearer {access_token}"},
    )


def test_second_login_kicks_out_first_device(client, app):
    db = _cdb(app)
    db.create_user("bd_tester1", "pass1234", role="sales_executive", workspace_id="ws_bd")

    first = _login(client, "bd_tester1", device="phone")
    assert _probe(client, first["access_token"]).status_code == 200

    second = _login(client, "bd_tester1", device="desktop")

    old = _probe(client, first["access_token"])
    assert old.status_code == 401
    assert old.get_json()["error"]["code"] == "SESSION_REVOKED"

    assert _probe(client, second["access_token"]).status_code == 200


def test_kicked_device_cannot_refresh(client, app):
    db = _cdb(app)
    db.create_user("bd_tester1", "pass1234", role="sales_executive", workspace_id="ws_bd")

    first = _login(client, "bd_tester1", device="phone")
    _login(client, "bd_tester1", device="desktop")

    res = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "SESSION_REVOKED"


def test_workspace_owner_may_use_two_devices(client, app):
    db = _cdb(app)
    owner = db.create_user(
        "kunwar1del", "pass1234", role="sales_executive", workspace_id="ws_bd"
    )
    db.promote_workspace_owner("kunwar1del")

    first = _login(client, "kunwar1del", device="phone")
    _login(client, "kunwar1del", device="desktop")

    assert _probe(client, first["access_token"]).status_code == 200
    assert db.is_multi_device_allowed(owner["id"] if isinstance(owner, dict) else owner)


def test_owner_can_grant_multi_device_to_a_user(client, app):
    db = _cdb(app)
    db.create_user("kunwar1del", "pass1234", role="sales_executive", workspace_id="ws_bd")
    db.promote_workspace_owner("kunwar1del")
    target = db.create_user(
        "bd_tester1", "pass1234", role="sales_executive", workspace_id="ws_bd"
    )
    target_id = target["id"] if isinstance(target, dict) else target

    owner_login = _login(client, "kunwar1del")
    res = client.post(
        f"/api/v1/admin/users/{target_id}/multi-device",
        json={"allowed": True},
        headers={"Authorization": f"Bearer {owner_login['access_token']}"},
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["multi_device_allowed"] is True

    first = _login(client, "bd_tester1", device="phone")
    _login(client, "bd_tester1", device="desktop")
    assert _probe(client, first["access_token"]).status_code == 200


def test_non_owner_cannot_grant_multi_device(client, app):
    db = _cdb(app)
    db.create_user("kunwar1del", "pass1234", role="sales_executive", workspace_id="ws_bd")
    db.promote_workspace_owner("kunwar1del")
    other = db.create_user(
        "bd_tester1", "pass1234", role="sales_executive", workspace_id="ws_bd"
    )
    other_id = other["id"] if isinstance(other, dict) else other

    login = _login(client, "bd_tester1")
    res = client.post(
        f"/api/v1/admin/users/{other_id}/multi-device",
        json={"allowed": True},
        headers={"Authorization": f"Bearer {login['access_token']}"},
    )
    assert res.status_code in (401, 403)


def test_phone_and_computer_together_and_founder_gift(client, app):
    db = _cdb(app)
    shop = db.create_user("shop_one", "pass1234", role="admin", workspace_id="ws_shop")
    db.create_user("kunwar1del", "ownerpass1", role="admin", workspace_id="ws_owner")
    db.promote_workspace_owner("kunwar1del")
    phone = _login(client, "shop_one", device="Fold")                      # test client = phone app
    web = client.post("/api/v1/auth/login", json={"username": "shop_one", "password": "pass1234", "client": "web"}).get_json()["data"]
    assert _probe(client, phone["access_token"]).status_code != 401        # a phone and a computer together
    assert _probe(client, web["access_token"]).status_code != 401
    second = _login(client, "shop_one", device="Vivo")
    assert _probe(client, phone["access_token"]).status_code == 401        # the second phone ends the first
    assert _probe(client, second["access_token"]).status_code != 401
    owner = _login(client, "kunwar1del", password="ownerpass1")
    h = {"Authorization": f"Bearer {owner['access_token']}"}
    uid = shop["id"] if isinstance(shop, dict) else shop
    rows = client.get("/api/v1/admin/logins?q=shop_one", headers=h).get_json()["data"]["logins"]
    assert rows and rows[0]["phones"] == 1 and len(rows[0]["devices"]) == 2
    res = client.post(f"/api/v1/admin/users/{uid}/devices", json={"phones": 2, "computers": 1}, headers=h)
    assert res.status_code == 200 and res.get_json()["data"]["phones"] == 2
    third = _login(client, "shop_one", device="Tab")                       # gifted: two phones live
    assert _probe(client, second["access_token"]).status_code != 401
    assert _probe(client, third["access_token"]).status_code != 401
    other = client.post(f"/api/v1/admin/users/{uid}/devices", json={"phones": 5},
                        headers={"Authorization": f"Bearer {third['access_token']}"})
    assert other.status_code == 403

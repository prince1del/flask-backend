"""PUT edit routes for distributor payments (deposit + SO payment entry)."""

from __future__ import annotations

import importlib


def _setup_app(tmp_path, monkeypatch):
    db_path = tmp_path / "payment_edit_api.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "payment-edit-test-key")

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)

    app = web_app_module.create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    from centralized_db_system.db import CentralizedDB

    CentralizedDB(str(db_path)).create_user("pay_user", "pass123", role="sales_executive", workspace_id="ws-1")
    login = client.post("/api/v1/auth/login", json={"username": "pay_user", "password": "pass123"})
    assert login.status_code == 200, login.get_data(as_text=True)
    return client, {"Authorization": f"Bearer {login.get_json()['data']['access_token']}"}


def test_edit_distributor_deposit(tmp_path, monkeypatch):
    client, headers = _setup_app(tmp_path, monkeypatch)
    added = client.post(
        "/api/v1/distributor-payments/deposits",
        json={"distributor_id": 7, "season": "AW26", "category": "Bed", "amount": 10000,
              "payment_date": "2026-08-01", "note": "typo"},
        headers=headers,
    )
    assert added.status_code == 200, added.get_data(as_text=True)
    dep_id = added.get_json()["data"]["entry"]["id"]

    resp = client.put(f"/api/v1/distributor-payments/deposits/{dep_id}",
                      json={"amount": 100000, "payment_date": "2026-08-02", "note": "NEFT"}, headers=headers)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    entry = resp.get_json()["data"]["entry"]
    assert (entry["amount"], entry["payment_date"], entry["note"]) == (100000, "2026-08-02", "NEFT")

    assert client.put(f"/api/v1/distributor-payments/deposits/{dep_id}",
                      json={"amount": 0}, headers=headers).status_code == 400
    assert client.put(f"/api/v1/distributor-payments/deposits/{dep_id}",
                      json={"amount": "abc"}, headers=headers).status_code == 400
    assert client.put("/api/v1/distributor-payments/deposits/99999",
                      json={"amount": 5}, headers=headers).status_code == 404
    assert client.put(f"/api/v1/distributor-payments/deposits/{dep_id}", json={"amount": 5}).status_code == 401


def test_edit_payment_collection_entry_errors(tmp_path, monkeypatch):
    client, headers = _setup_app(tmp_path, monkeypatch)
    url = "/api/v1/order-fulfillment/payment-collection/entries/99999"
    assert client.put(url, json={"amount": "abc"}, headers=headers).status_code == 400
    assert client.put(url, json={"amount": 5}, headers=headers).status_code == 404

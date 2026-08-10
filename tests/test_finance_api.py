"""Finance API smoke tests + ledger account_id footgun guard."""

from __future__ import annotations

import importlib
import os

os.environ.setdefault("SECRET_KEY", "finance-api-test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "finance-api-test-secret")

from centralized_db_system.db import CentralizedDB


def _auth_client(tmp_path, monkeypatch):
    db_path = tmp_path / "finance_api.sqlite3"

    def _apply():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "finance-api-test-secret")
        monkeypatch.setenv("JWT_SECRET_KEY", "finance-api-test-secret")

    _apply()
    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply()

    app = web_app_module.create_app()
    app.config.update(TESTING=True, DATABASE_PATH=str(db_path))
    CentralizedDB(str(db_path)).create_user(
        "finance_tester", "pass123", role="admin", workspace_id="default"
    )
    client = app.test_client()
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "finance_tester", "password": "pass123"},
    )
    assert login.status_code == 200, login.get_data(as_text=True)
    token = login.get_json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    return client, headers


def test_dashboard_renders_finance_modals():
    from pathlib import Path

    html = Path("app/templates/index.html").read_text(encoding="utf-8")
    assert 'id="finance-account-modal"' in html
    assert 'id="finance-gst-modal"' in html
    assert 'id="finance-vat-modal"' in html


def test_advanced_finance_reports_are_available(tmp_path, monkeypatch):
    client, headers = _auth_client(tmp_path, monkeypatch)

    ledger_response = client.get("/api/v1/finance/ledger", headers=headers)
    assert ledger_response.status_code == 200
    assert ledger_response.get_json()["success"] is True

    bad_account = client.get("/api/v1/finance/ledger?account_id=1", headers=headers)
    assert bad_account.status_code == 400
    assert bad_account.get_json()["success"] is False
    assert bad_account.get_json()["error"]["code"] == "NOT_SUPPORTED"

    trial_response = client.get("/api/v1/finance/trial-balance", headers=headers)
    assert trial_response.status_code == 200
    assert trial_response.get_json()["success"] is True

    statements_response = client.get(
        "/api/v1/finance/financial-statements", headers=headers
    )
    assert statements_response.status_code == 200
    assert statements_response.get_json()["success"] is True


def test_finance_account_and_tax_endpoints(tmp_path, monkeypatch):
    client, headers = _auth_client(tmp_path, monkeypatch)

    account_response = client.post(
        "/api/v1/finance/accounts",
        headers=headers,
        json={
            "name": "Cash",
            "account_type": "asset",
            "opening_balance": 1000.0,
            "notes": "Primary cash account",
        },
    )
    assert account_response.status_code == 200
    account_json = account_response.get_json()
    assert account_json["success"] is True
    assert account_json["data"]["name"] == "Cash"

    list_accounts = client.get("/api/v1/finance/accounts", headers=headers)
    assert list_accounts.status_code == 200
    accounts_json = list_accounts.get_json()
    assert any(item["name"] == "Cash" for item in accounts_json["data"])

    gst_response = client.post(
        "/api/v1/finance/gst",
        headers=headers,
        json={
            "period": "2026-06",
            "sales_amount": 1000.0,
            "purchase_amount": 800.0,
            "tax_rate": 18.0,
            "filed_status": "draft",
            "notes": "Quarterly GST draft",
        },
    )
    assert gst_response.status_code == 200
    gst_json = gst_response.get_json()
    assert gst_json["success"] is True
    assert gst_json["data"]["period"] == "2026-06"

    vat_response = client.post(
        "/api/v1/finance/vat",
        headers=headers,
        json={
            "period": "2026-06",
            "sales_amount": 1200.0,
            "purchase_amount": 900.0,
            "tax_rate": 12.0,
            "filed_status": "filed",
            "notes": "VAT return filed",
        },
    )
    assert vat_response.status_code == 200
    vat_json = vat_response.get_json()
    assert vat_json["success"] is True
    assert vat_json["data"]["period"] == "2026-06"

    gst_list = client.get("/api/v1/finance/gst", headers=headers)
    assert gst_list.status_code == 200
    gst_items = gst_list.get_json()["data"]
    assert any(item["period"] == "2026-06" for item in gst_items)

    vat_list = client.get("/api/v1/finance/vat", headers=headers)
    assert vat_list.status_code == 200
    vat_items = vat_list.get_json()["data"]
    assert any(item["period"] == "2026-06" for item in vat_items)

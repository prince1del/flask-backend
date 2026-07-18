from app.web_app import create_app


def test_dashboard_renders_finance_modals():
    app = create_app()

    with app.test_client() as client:
        response = client.get("/")
        assert response.status_code == 200
        html = response.text
        assert 'id="finance-account-modal"' in html
        assert 'id="finance-gst-modal"' in html
        assert 'id="finance-vat-modal"' in html


def test_advanced_finance_reports_are_available():
    app = create_app()

    with app.test_client() as client:
        ledger_response = client.get("/api/v1/finance/ledger")
        assert ledger_response.status_code == 200
        assert ledger_response.get_json()["success"] is True

        trial_response = client.get("/api/v1/finance/trial-balance")
        assert trial_response.status_code == 200
        assert trial_response.get_json()["success"] is True

        statements_response = client.get("/api/v1/finance/financial-statements")
        assert statements_response.status_code == 200
        assert statements_response.get_json()["success"] is True


def test_finance_account_and_tax_endpoints():
    app = create_app()

    with app.test_client() as client:
        account_response = client.post(
            "/api/v1/finance/accounts",
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

        list_accounts = client.get("/api/v1/finance/accounts")
        assert list_accounts.status_code == 200
        accounts_json = list_accounts.get_json()
        assert any(item["name"] == "Cash" for item in accounts_json["data"])

        gst_response = client.post(
            "/api/v1/finance/gst",
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

        gst_list = client.get("/api/v1/finance/gst")
        assert gst_list.status_code == 200
        gst_items = gst_list.get_json()["data"]
        assert any(item["period"] == "2026-06" for item in gst_items)

        vat_list = client.get("/api/v1/finance/vat")
        assert vat_list.status_code == 200
        vat_items = vat_list.get_json()["data"]
        assert any(item["period"] == "2026-06" for item in vat_items)

import pytest

from app.web_app import create_app


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def create_distributor(client, name="Sales Distributor", gst="GST-SALES-001"):
    response = client.post(
        "/api/v1/parties/distributors",
        json={"name": name, "gst_number": gst},
    )
    assert response.status_code == 201
    return response.get_json()["data"]


def create_retailer(client, distributor_id, name="Test Retailer", gst="GST-RETAIL-001"):
    response = client.post(
        "/api/v1/parties/retailers",
        json={"name": name, "distributor_id": distributor_id, "gst_number": gst},
    )
    assert response.status_code == 201
    return response.get_json()["data"]


def test_create_sales_order(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])

    response = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": distributor["id"],
            "retailer_id": retailer["id"],
            "items": [
                {"product_code": "P001", "product_name": "Product One", "quantity": 2, "unit_price": 100.0},
            ],
            "tax_rate": 18,
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["success"] is True
    order = payload["data"]
    assert order["status"] == "draft"
    assert order["total_amount"] == 200.0
    assert order["tax_amount"] == 36.0
    assert order["net_amount"] == 236.0
    assert order["items"][0]["product_code"] == "P001"


def test_list_sales_orders(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])

    client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": distributor["id"],
            "retailer_id": retailer["id"],
            "items": [
                {"product_code": "P002", "product_name": "Product Two", "quantity": 1, "unit_price": 150.0},
            ],
            "tax_rate": 12,
        },
    )

    response = client.get("/api/v1/sales-orders")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["count"] == 1
    assert payload["data"]["results"]


def test_get_sales_order_by_id(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])

    create_response = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": distributor["id"],
            "retailer_id": retailer["id"],
            "items": [
                {"product_code": "P003", "product_name": "Product Three", "quantity": 4, "unit_price": 75.0},
            ],
            "tax_rate": 10,
        },
    )
    order_id = create_response.get_json()["data"]["id"]

    response = client.get(f"/api/v1/sales-orders/{order_id}")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["id"] == order_id
    assert payload["data"]["items"]


def test_update_sales_order(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])

    create_response = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": distributor["id"],
            "retailer_id": retailer["id"],
            "items": [
                {"product_code": "P004", "product_name": "Product Four", "quantity": 2, "unit_price": 50.0},
            ],
            "tax_rate": 5,
        },
    )
    order_id = create_response.get_json()["data"]["id"]

    response = client.put(
        f"/api/v1/sales-orders/{order_id}",
        json={
            "items": [
                {"product_code": "P004", "product_name": "Product Four", "quantity": 3, "unit_price": 60.0},
            ],
            "tax_rate": 10,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["total_amount"] == 180.0
    assert payload["data"]["tax_amount"] == 18.0
    assert payload["data"]["net_amount"] == 198.0


def test_update_sales_order_tax_rate_recomputes_totals(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])

    create_response = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": distributor["id"],
            "retailer_id": retailer["id"],
            "items": [
                {"product_code": "P006", "product_name": "Product Six", "quantity": 2, "unit_price": 100.0},
            ],
            "tax_rate": 10,
        },
    )
    order_id = create_response.get_json()["data"]["id"]

    response = client.put(f"/api/v1/sales-orders/{order_id}", json={"tax_rate": 20})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["total_amount"] == 200.0
    assert payload["data"]["tax_amount"] == 40.0
    assert payload["data"]["net_amount"] == 240.0


def test_create_sales_order_rejects_invalid_tax_rate(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])

    response = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": distributor["id"],
            "retailer_id": retailer["id"],
            "items": [
                {"product_code": "P007", "product_name": "Product Seven", "quantity": 1, "unit_price": 90.0},
            ],
            "tax_rate": "invalid",
        },
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "Invalid tax rate" in payload["message"]


def test_update_sales_order_status(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])

    create_response = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": distributor["id"],
            "retailer_id": retailer["id"],
            "items": [
                {"product_code": "P005", "product_name": "Product Five", "quantity": 1, "unit_price": 100.0},
            ],
            "tax_rate": 0,
        },
    )
    order_id = create_response.get_json()["data"]["id"]

    response = client.put(f"/api/v1/sales-orders/{order_id}/status", json={"status": "approved"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["status"] == "approved"


def test_sales_order_missing_distributor_id(client):
    response = client.post(
        "/api/v1/sales-orders",
        json={
            "retailer_id": 1,
            "items": [{"product_code": "P006", "product_name": "Product Six", "quantity": 1, "unit_price": 90.0}],
        },
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "Distributor ID is required" in payload["message"]


def test_sales_order_invalid_distributor_id(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])

    response = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": 9999,
            "retailer_id": retailer["id"],
            "items": [{"product_code": "P007", "product_name": "Product Seven", "quantity": 1, "unit_price": 85.0}],
        },
    )
    assert response.status_code == 404
    payload = response.get_json()
    assert payload["success"] is False
    assert "Distributor not found" in payload["message"]

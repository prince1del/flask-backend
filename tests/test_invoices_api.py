import os
import pytest
from app.web_app import create_app
from app.db import db


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "invoices_api.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


def create_distributor(client, name="Invoice Distributor", gst="GST-INVOICE-001"):
    response = client.post(
        "/api/v1/parties/distributors",
        json={"name": name, "gst_number": gst},
    )
    assert response.status_code == 201
    return response.get_json()["data"]


def create_retailer(client, distributor_id, name="Invoice Retailer", gst="GST-RETAIL-001"):
    response = client.post(
        "/api/v1/parties/retailers",
        json={"name": name, "distributor_id": distributor_id, "gst_number": gst},
    )
    assert response.status_code == 201
    return response.get_json()["data"]


def create_sales_order(client, distributor_id, retailer_id):
    response = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": distributor_id,
            "retailer_id": retailer_id,
            "items": [
                {"product_code": "INV001", "product_name": "Invoice Product", "quantity": 2, "unit_price": 75.0},
            ],
            "tax_rate": 10,
        },
    )
    assert response.status_code == 201
    return response.get_json()["data"]


def test_create_invoice(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])
    order = create_sales_order(client, distributor["id"], retailer["id"])

    response = client.post(
        "/api/v1/invoices",
        json={"so_id": order["id"], "due_date": "2026-07-31"},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["success"] is True
    invoice = payload["data"]
    assert invoice["so_id"] == order["id"]
    assert invoice["payment_status"] == "unpaid"


def test_list_invoices(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])
    order = create_sales_order(client, distributor["id"], retailer["id"])
    client.post("/api/v1/invoices", json={"so_id": order["id"], "due_date": "2026-07-31"})

    response = client.get("/api/v1/invoices")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["count"] == 1
    assert payload["data"]["results"]


def test_get_invoice_by_id(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])
    order = create_sales_order(client, distributor["id"], retailer["id"])
    invoice = client.post("/api/v1/invoices", json={"so_id": order["id"], "due_date": "2026-07-31"}).get_json()["data"]

    response = client.get(f"/api/v1/invoices/{invoice['id']}")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["id"] == invoice["id"]
    assert payload["data"]["payments"] == []


def test_record_payment(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])
    order = create_sales_order(client, distributor["id"], retailer["id"])
    invoice = client.post("/api/v1/invoices", json={"so_id": order["id"], "due_date": "2026-07-31"}).get_json()["data"]

    response = client.post(
        f"/api/v1/invoices/{invoice['id']}/payment",
        json={"amount_paid": 50.0, "payment_method": "bank_transfer", "reference_number": "PAY123"},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["paid_amount"] == 50.0
    assert payload["data"]["payment_status"] == "partial"


def test_create_dispatch(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])
    order = create_sales_order(client, distributor["id"], retailer["id"])

    response = client.post(
        "/api/v1/orders/dispatch",
        json={"so_id": order["id"], "tracking_number": "TRACK-001", "vehicle": "Truck A"},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["so_id"] == order["id"]
    assert payload["data"]["tracking_number"] == "TRACK-001"


def test_invoice_missing_so_id(client):
    response = client.post("/api/v1/invoices", json={"due_date": "2026-07-31"})
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "Sales order ID is required" in payload["message"]


def test_invoice_invalid_so_id(client):
    response = client.post("/api/v1/invoices", json={"so_id": 9999, "due_date": "2026-07-31"})
    assert response.status_code == 404
    payload = response.get_json()
    assert payload["success"] is False
    assert "Sales order not found" in payload["message"]

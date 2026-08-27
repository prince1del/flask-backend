import importlib

import pytest

from centralized_db_system.db import CentralizedDB
from app.db import db as sqlalchemy_db
from app.models import Distributor, Retailer
from app.web_app import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "sales_orders_api.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        sqlalchemy_db.create_all()
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    db_path = tmp_path / "sales_orders_auth.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)

    app = web_app_module.create_app()
    app.config["TESTING"] = True

    db = CentralizedDB(str(db_path))
    db.create_user("user_a", "pass123", role="sales_executive", workspace_id="ws-1")
    db.create_user("user_b", "pass123", role="sales_executive", workspace_id="ws-2")

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


def test_sales_orders_respect_workspace_from_auth_token(auth_client):
    client = auth_client

    def login(username: str, password: str) -> str:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert response.status_code == 200
        return response.get_json()["data"]["access_token"]

    token_a = login("user_a", "pass123")
    token_b = login("user_b", "pass123")

    with client.application.app_context():
        dist_a = Distributor(
            name="Dist A",
            contact_person="",
            phone="",
            email=None,
            address=None,
            city=None,
            state=None,
            gst_number="GST-A",
            credit_limit=0.0,
            workspace_id="ws-1",
            status="active",
        )
        sqlalchemy_db.session.add(dist_a)
        sqlalchemy_db.session.flush()

        retailer_a = Retailer(
            name="Retail A",
            contact_person="",
            phone="",
            email=None,
            address=None,
            city=None,
            state=None,
            gst_number="GST-RET-A",
            distributor_id=dist_a.id,
            workspace_id="ws-1",
            status="active",
        )
        sqlalchemy_db.session.add(retailer_a)
        sqlalchemy_db.session.flush()

        dist_b = Distributor(
            name="Dist B",
            contact_person="",
            phone="",
            email=None,
            address=None,
            city=None,
            state=None,
            gst_number="GST-B",
            credit_limit=0.0,
            workspace_id="ws-2",
            status="active",
        )
        sqlalchemy_db.session.add(dist_b)
        sqlalchemy_db.session.flush()

        retailer_b = Retailer(
            name="Retail B",
            contact_person="",
            phone="",
            email=None,
            address=None,
            city=None,
            state=None,
            gst_number="GST-RET-B",
            distributor_id=dist_b.id,
            workspace_id="ws-2",
            status="active",
        )
        sqlalchemy_db.session.add(retailer_b)
        sqlalchemy_db.session.commit()

        dist_a_id = dist_a.id
        retailer_a_id = retailer_a.id
        dist_b_id = dist_b.id
        retailer_b_id = retailer_b.id

    create_response_a = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": dist_a_id,
            "retailer_id": retailer_a_id,
            "items": [{"product_code": "A1", "product_name": "Item A", "quantity": 1, "unit_price": 100.0}],
            "tax_rate": 0,
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert create_response_a.status_code == 201
    order_a = create_response_a.get_json()["data"]

    create_response_b = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": dist_b_id,
            "retailer_id": retailer_b_id,
            "items": [{"product_code": "B1", "product_name": "Item B", "quantity": 1, "unit_price": 200.0}],
            "tax_rate": 0,
        },
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert create_response_b.status_code == 201

    list_response = client.get(
        "/api/v1/sales-orders",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert list_response.status_code == 200
    payload = list_response.get_json()
    assert payload["success"] is True
    assert len(payload["data"]["results"]) == 1
    assert payload["data"]["results"][0]["id"] == order_a["id"]
    assert payload["data"]["results"][0]["workspace_id"] == "ws-1"

    tampered_response = client.get(
        "/api/v1/sales-orders?workspace_id=ws-2",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert tampered_response.status_code == 200
    tampered_payload = tampered_response.get_json()
    assert len(tampered_payload["data"]["results"]) == 1
    assert tampered_payload["data"]["results"][0]["workspace_id"] == "ws-1"

    unauthenticated = client.get("/api/v1/sales-orders")
    assert unauthenticated.status_code == 401


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


def test_create_invoice_for_sales_order(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])

    create_response = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": distributor["id"],
            "retailer_id": retailer["id"],
            "items": [
                {"product_code": "P008", "product_name": "Product Eight", "quantity": 2, "unit_price": 120.0},
            ],
            "tax_rate": 12,
        },
    )
    order_id = create_response.get_json()["data"]["id"]

    invoice_response = client.post(
        "/api/v1/invoices",
        json={
            "so_id": order_id,
            "invoice_date": "2026-06-29",
            "due_date": "2026-07-05"
        },
    )

    assert invoice_response.status_code == 201
    payload = invoice_response.get_json()
    assert payload["success"] is True
    assert payload["data"]["invoice_number"].startswith("INV-")
    assert payload["data"]["so_id"] == order_id
    assert payload["data"]["payment_status"] == "unpaid"


def test_invoice_creation_posts_tax_to_gst_and_vat_returns(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])

    create_response = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": distributor["id"],
            "retailer_id": retailer["id"],
            "items": [
                {"product_code": "P012", "product_name": "Product Twelve", "quantity": 1, "unit_price": 100.0},
            ],
            "tax_rate": 10,
        },
    )
    order_id = create_response.get_json()["data"]["id"]

    invoice_response = client.post(
        "/api/v1/invoices",
        json={"so_id": order_id, "invoice_date": "2026-06-29", "due_date": "2026-07-05"},
    )
    assert invoice_response.status_code == 201

    gst_response = client.get("/api/v1/finance/gst")
    vat_response = client.get("/api/v1/finance/vat")

    assert gst_response.status_code == 200
    assert vat_response.status_code == 200

    gst_items = gst_response.get_json()["data"]
    vat_items = vat_response.get_json()["data"]

    assert any(item["sales_amount"] == 100.0 and item["tax_amount"] == 10.0 and item["tax_rate"] == 10.0 for item in gst_items)
    assert any(item["sales_amount"] == 100.0 and item["tax_amount"] == 10.0 and item["tax_rate"] == 10.0 for item in vat_items)


def test_create_invoice_rejects_duplicate(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])
    create_response = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": distributor["id"],
            "retailer_id": retailer["id"],
            "items": [
                {"product_code": "P009", "product_name": "Product Nine", "quantity": 1, "unit_price": 100.0},
            ],
            "tax_rate": 10,
        },
    )
    order_id = create_response.get_json()["data"]["id"]

    client.post(
        "/api/v1/invoices",
        json={"so_id": order_id, "invoice_date": "2026-06-29", "due_date": "2026-07-05"},
    )

    duplicate_response = client.post(
        "/api/v1/invoices",
        json={"so_id": order_id, "invoice_date": "2026-06-29", "due_date": "2026-07-05"},
    )
    assert duplicate_response.status_code == 400
    payload = duplicate_response.get_json()
    assert payload["success"] is False
    assert "Invoice already exists" in payload["message"]


def test_record_invoice_payment_and_status_update(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])
    create_response = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": distributor["id"],
            "retailer_id": retailer["id"],
            "items": [
                {"product_code": "P010", "product_name": "Product Ten", "quantity": 1, "unit_price": 200.0},
            ],
            "tax_rate": 0,
        },
    )
    order_id = create_response.get_json()["data"]["id"]
    invoice_response = client.post(
        "/api/v1/invoices",
        json={"so_id": order_id, "invoice_date": "2026-06-29", "due_date": "2026-07-05"},
    )
    invoice_id = invoice_response.get_json()["data"]["id"]

    payment_response = client.post(
        f"/api/v1/invoices/{invoice_id}/payment",
        json={"amount_paid": 200.0, "payment_method": "cash", "payment_date": "2026-06-29"},
    )
    assert payment_response.status_code == 201
    payload = payment_response.get_json()
    assert payload["success"] is True
    assert payload["data"]["payment_status"] == "paid"


def test_dispatch_sales_order(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])
    create_response = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": distributor["id"],
            "retailer_id": retailer["id"],
            "items": [
                {"product_code": "P011", "product_name": "Product Eleven", "quantity": 2, "unit_price": 50.0},
            ],
            "tax_rate": 0,
        },
    )
    order_id = create_response.get_json()["data"]["id"]

    dispatch_response = client.post(
        "/api/v1/orders/dispatch",
        json={"so_id": order_id, "dispatch_date": "2026-06-29", "vehicle": "Truck 1", "tracking_number": "TRACK123"},
    )
    assert dispatch_response.status_code == 201
    payload = dispatch_response.get_json()
    assert payload["success"] is True
    assert payload["data"]["status"] == "dispatched"


def test_get_invoices_list(client):
    distributor = create_distributor(client)
    retailer = create_retailer(client, distributor_id=distributor["id"])
    create_response = client.post(
        "/api/v1/sales-orders",
        json={
            "distributor_id": distributor["id"],
            "retailer_id": retailer["id"],
            "items": [
                {"product_code": "P012", "product_name": "Product Twelve", "quantity": 1, "unit_price": 120.0},
            ],
            "tax_rate": 10,
        },
    )
    order_id = create_response.get_json()["data"]["id"]
    client.post("/api/v1/invoices", json={"so_id": order_id, "invoice_date": "2026-06-29", "due_date": "2026-07-05"})

    response = client.get("/api/v1/invoices")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["count"] >= 1

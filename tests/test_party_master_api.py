import os
import pytest

from app.web_app import create_app
from app.db import db


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "party_master_api.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


def test_create_update_delete_distributor(client):
    response = client.post(
        "/api/v1/parties/distributors",
        json={"name": "Test Distributor", "gst_number": "GST1234"},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["success"] is True
    distributor = payload["data"]
    assert distributor["name"] == "Test Distributor"
    assert distributor["gst_number"] == "GST1234"

    distributor_id = distributor["id"]

    response = client.put(
        f"/api/v1/parties/distributors/{distributor_id}",
        json={"city": "Mumbai", "territory": "West"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["city"] == "Mumbai"
    assert payload["data"]["territory"] == "West"

    response = client.get("/api/v1/parties/distributors")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["count"] == 1

    response = client.delete(f"/api/v1/parties/distributors/{distributor_id}")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["message"] == "Distributor deleted successfully"

    response = client.get("/api/v1/parties/distributors")
    payload = response.get_json()
    assert payload["data"]["count"] == 0


def test_create_update_delete_retailer(client):
    distributor_response = client.post(
        "/api/v1/parties/distributors",
        json={"name": "Parent Distributor", "gst_number": "GST5678"},
    )
    assert distributor_response.status_code == 201
    distributor_id = distributor_response.get_json()["data"]["id"]

    retailer_response = client.post(
        "/api/v1/parties/retailers",
        json={
            "name": "Test Retailer",
            "distributor_id": distributor_id,
            "gst_number": "GST9999",
        },
    )
    assert retailer_response.status_code == 201
    payload = retailer_response.get_json()
    assert payload["success"] is True
    retailer = payload["data"]
    assert retailer["name"] == "Test Retailer"
    assert retailer["distributor_id"] == distributor_id

    retailer_id = retailer["id"]

    response = client.put(
        f"/api/v1/parties/retailers/{retailer_id}",
        json={"city": "Pune", "store_type": "Modern Trade"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["city"] == "Pune"
    assert payload["data"]["store_type"] == "Modern Trade"

    response = client.get("/api/v1/parties/retailers")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["data"]["count"] == 1

    response = client.delete(f"/api/v1/parties/retailers/{retailer_id}")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True

    response = client.get("/api/v1/parties/retailers")
    payload = response.get_json()
    assert payload["data"]["count"] == 0


def test_retailer_requires_distributor(client):
    response = client.post(
        "/api/v1/parties/retailers",
        json={"name": "Retailer Without Distributor"},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "Distributor ID is required" in payload["message"]

from app.web_app import create_app
import json


def test_operations_api_endpoints(tmp_path, monkeypatch):
    # Disable auth for tests
    monkeypatch.setenv("AUTH_ENABLED", "false")
    db_path = str(tmp_path / "api.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    app = create_app()
    client = app.test_client()

    # DB will be created in default path under project; no auth required in tests
    # Create distributor and lifecycle via CentralizedDB for a tracking reference
    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(db_path)
    distributor_id = db.add_master_distributor(name="Ops D", buyer_code="OP1")
    tracking_id = db.create_order_lifecycle_tracking(order_ref_no="SO-800", distributor_id=distributor_id)

    # dispatch
    resp = client.post(
        "/api/v1/operations/dispatch",
        json={
            "tracking_id": tracking_id,
            "pod_number": "POD-800",
            "driver_name": "Bob",
            "vehicle_number": "V-99",
            "dispatched_at": "2026-07-01T08:00:00Z",
            "delivered_at": "2026-07-02T10:00:00Z",
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True

    # returns
    resp = client.post(
        "/api/v1/operations/returns",
        json={"tracking_id": tracking_id, "product_code": "Widget", "returned_qty": 1, "reason": "Damaged"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True

    # reconcile invoice
    resp = client.post(
        "/api/v1/operations/invoices/reconcile",
        json={"tracking_id": tracking_id, "invoice_number": "INV-800", "invoice_date": "2026-07-02", "invoice_amount": 100.0, "reconciled": True},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True

    # list alerts
    resp = client.get("/api/v1/operations/alerts")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True

from app.web_app import create_app


def test_phase2_8_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    db_path = str(tmp_path / "p28api.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    app = create_app()
    client = app.test_client()

    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(db_path)
    distributor_id = db.add_master_distributor(name="D28api", buyer_code="B28API")
    tracking_id = db.create_order_lifecycle_tracking(order_ref_no="SO-910", distributor_id=distributor_id)

    pod_id = db.record_dispatch_pod(tracking_id=tracking_id, pod_number="POD-910", workspace_id="default")

    # attach pod (no OCR available in test environment)
    resp = client.post("/api/v1/phase2_8/pod/attach", json={"pod_id": pod_id, "attachment_reference": "path/does/not/exist.jpg"})
    assert resp.status_code == 200

    # create reconciliation -> invoice
    rec_id = db.reconcile_invoice(tracking_id=tracking_id, invoice_number="INV-910", invoice_date="2026-07-04", invoice_amount=300.0, reconciled=True, workspace_id="default")
    resp = client.post("/api/v1/phase2_8/invoices/from-reconciliation", json={"reconciliation_id": rec_id})
    assert resp.status_code == 200

    # inventory adjust
    resp = client.post("/api/v1/phase2_8/inventory/adjust", json={"article_code": "ART-X", "adjustment_qty": -2, "reason": "Return"})
    assert resp.status_code == 200

    # notifications
    resp = client.post("/api/v1/phase2_8/notifications/subscribe", json={"target": "test", "channel": "email", "address": "a@b.com"})
    assert resp.status_code == 200
    resp = client.post("/api/v1/phase2_8/notifications/send", json={"target": "test", "message": "hello"})
    assert resp.status_code == 200

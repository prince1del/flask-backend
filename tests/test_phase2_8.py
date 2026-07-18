from centralized_db_system.db import CentralizedDB


def test_attach_pod_ocr_and_invoice_creation(tmp_path):
    db_path = str(tmp_path / "p28.db")
    db = CentralizedDB(db_path)

    distributor_id = db.add_master_distributor(name="D28", buyer_code="B28")
    tracking_id = db.create_order_lifecycle_tracking(order_ref_no="SO-900", distributor_id=distributor_id)

    pod_id = db.record_dispatch_pod(tracking_id=tracking_id, pod_number="POD-900", dispatched_at="2026-07-01T00:00:00Z", workspace_id="default")
    attached = db.attach_pod_ocr(pod_id, pod_text="Sample OCR text", attachment_reference="files/pod_900.jpg")
    assert attached["id"] == pod_id

    rec_id = db.reconcile_invoice(tracking_id=tracking_id, invoice_number="INV-900", invoice_date="2026-07-02", invoice_amount=200.0, reconciled=True, workspace_id="default")
    inv_id = db.create_invoice_from_reconciliation(rec_id, workspace_id="default")
    assert inv_id > 0


def test_inventory_adjustments_and_notifications(tmp_path):
    db_path = str(tmp_path / "p28b.db")
    db = CentralizedDB(db_path)

    adj_id = db.apply_inventory_adjustment(article_code="ART-1", adjustment_qty=-5.0, reason="Return", related_tracking_id=None, workspace_id="default")
    assert adj_id > 0

    sub_id = db.create_notification_subscription(target="inventory-low", channel="email", address="ops@example.com", workspace_id="default")
    assert sub_id > 0

    alert_id = db.send_notification("inventory-low", "Stock below threshold for ART-1", workspace_id="default")
    assert alert_id > 0

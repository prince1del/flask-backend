import pytest
from centralized_db_system.db import CentralizedDB


def test_record_dispatch_pod_updates_lifecycle(tmp_path):
    db_path = str(tmp_path / "p27.db")
    db = CentralizedDB(db_path)

    distributor_id = db.add_master_distributor(name="Dist D", buyer_code="BXD")
    tracking_id = db.create_order_lifecycle_tracking(order_ref_no="SO-700", distributor_id=distributor_id)

    pod_id = db.record_dispatch_pod(
        tracking_id=tracking_id,
        pod_number="POD-700",
        driver_name="Alice",
        vehicle_number="VEH-1",
        dispatched_at="2026-07-01T08:00:00Z",
        delivered_at="2026-07-03T12:00:00Z",
        workspace_id="default",
    )
    assert pod_id > 0

    lifecycle = db.get_order_lifecycle_tracking(tracking_id)
    assert lifecycle["transit_status"] == "DELIVERED"
    assert lifecycle["pod_number"] == "POD-700"
    assert lifecycle["actual_delivery_date"] == "2026-07-03T12:00:00Z"


def test_record_return_claim_creates_alert(tmp_path):
    db_path = str(tmp_path / "p27r.db")
    db = CentralizedDB(db_path)

    distributor_id = db.add_master_distributor(name="Dist R", buyer_code="RX1")
    tracking_id = db.create_order_lifecycle_tracking(order_ref_no="SO-701", distributor_id=distributor_id)

    claim_id = db.record_return_claim(tracking_id=tracking_id, product_code="Widget", returned_qty=2, reason="Damaged", workspace_id="default")
    assert claim_id > 0

    alerts = db.list_alerts(workspace_id="default")
    assert any(a["alert_type"] == "return_claim" or "return claim" in a["message"].lower() for a in alerts)


def test_reconcile_invoice_marks_paid(tmp_path):
    db_path = str(tmp_path / "p27i.db")
    db = CentralizedDB(db_path)

    distributor_id = db.add_master_distributor(name="Dist I", buyer_code="IX1")
    tracking_id = db.create_order_lifecycle_tracking(order_ref_no="SO-702", distributor_id=distributor_id)

    rec_id = db.reconcile_invoice(tracking_id=tracking_id, invoice_number="INV-900", invoice_date="2026-06-30", invoice_amount=1500.0, reconciled=True, notes="Verified", workspace_id="default")
    assert rec_id > 0

    lifecycle = db.get_order_lifecycle_tracking(tracking_id)
    assert lifecycle["payment_status"] == "PAID"


def test_create_and_list_alerts(tmp_path):
    db_path = str(tmp_path / "p27a.db")
    db = CentralizedDB(db_path)

    aid = db.create_alert("test_alert", "ref-1", "This is a test", workspace_id="default")
    assert aid > 0
    alerts = db.list_alerts(workspace_id="default")
    assert any(a["alert_id"] == aid for a in alerts)

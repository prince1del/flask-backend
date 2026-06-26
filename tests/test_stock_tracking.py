from pathlib import Path

import pytest

from centralized_db_system.db import CentralizedDB


def test_order_lifecycle_delivery_requires_pod_and_actual_delivery_date(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "stock_tracking.sqlite3"))

    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="SO-1001",
        distributor_id=1,
        order_received_date="2026-06-01",
        expected_delivery_date="2026-06-05",
    )

    with pytest.raises(ValueError):
        db.update_order_lifecycle_status(tracking_id, "DELIVERED")

    updated = db.update_order_lifecycle_status(
        tracking_id,
        "DELIVERED",
        pod_number="POD-9001",
        actual_delivery_date="2026-06-04",
    )

    assert updated["transit_status"] == "DELIVERED"
    assert updated["pod_number"] == "POD-9001"
    assert updated["actual_delivery_date"] == "2026-06-04"
    assert len(db.list_status_history(tracking_id)) >= 2


def test_delivery_receipt_flags_partial_receipt_and_shortage(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "stock_receipts.sqlite3"))
    article_id = db.article_service.save_article(
        {
            "category_name": "towels",
            "design_code": "A100",
            "color_way": "blue",
            "base_rate": 1200,
            "gst_percentage": 5,
            "pcs_per_bale": 40,
        }
    )
    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="SO-1002",
        distributor_id=2,
        order_received_date="2026-06-02",
        expected_delivery_date="2026-06-06",
    )

    receipt = db.record_delivery_receipt(
        tracking_id=tracking_id,
        article_id=article_id,
        invoiced_qty=50,
        physically_received_qty=30,
        damaged_qty=5,
        verification_context={"invoiced_qty": 50},
    )

    assert receipt["status_flag"] == "MISMATCH_FOUND"
    assert receipt["shortage_qty"] == 20
    assert receipt["damaged_qty"] == 5
    assert db.get_order_lifecycle_tracking(tracking_id)["transit_status"] == "DISPATCHED"


def test_order_stage_updates_capture_payment_and_receiving_condition(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "stage_flow.sqlite3"))
    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="SO-1003",
        distributor_id=3,
        order_received_date="2026-06-10",
        expected_delivery_date="2026-06-15",
    )

    updated = db.update_order_lifecycle_stage(
        tracking_id=tracking_id,
        order_filled_date="2026-06-11",
        sales_order_generated_date="2026-06-11",
        payment_status="PARTIAL",
        commercial_invoice_date="2026-06-12",
        dispatch_date="2026-06-13",
        receiving_status="PARTIALLY_RECEIVED",
        receiving_condition="Damaged carton",
        notes="Partial delivery with carton damage",
    )

    assert updated["order_filled_date"] == "2026-06-11"
    assert updated["sales_order_generated_date"] == "2026-06-11"
    assert updated["payment_status"] == "PARTIAL"
    assert updated["commercial_invoice_date"] == "2026-06-12"
    assert updated["dispatch_date"] == "2026-06-13"
    assert updated["receiving_status"] == "PARTIALLY_RECEIVED"
    assert updated["receiving_condition"] == "Damaged carton"

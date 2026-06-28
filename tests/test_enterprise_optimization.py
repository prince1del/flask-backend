from pathlib import Path

from centralized_db_system.db import CentralizedDB


def test_real_time_validation_flags_rate_and_quantity_issues(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "enterprise.sqlite3"))

    result = db.process_data_entry(
        "Order Sheet",
        {
            "order_ref_no": "ORD-1001",
            "quantity": 10,
            "rate": 100,
            "amount": 900,
            "filled_qty": 8,
        },
        existing_entries=[{"order_ref_no": "ORD-1001"}],
    )

    assert result["accepted"] is False
    assert any("Rate mismatch" in warning for warning in result["warnings"])
    assert any("Quantity discrepancy" in warning for warning in result["warnings"])
    assert result["alert_id"] is not None


def test_credit_policy_schema_is_structured_and_bypassable(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "credit.sqlite3"))

    policy = db.validate_credit_policy(
        distributor_id=7,
        max_credit_limit=50000,
        credit_days_allowed=30,
        account_status="ACTIVE",
    )

    assert policy["valid"] is True
    assert policy["bypassed"] is True
    assert policy["policy"]["distributor_id"] == 7
    assert policy["policy"]["account_status"] == "ACTIVE"


def test_purchase_behavior_logs_aggregate_volume_and_frequency(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "behavior.sqlite3"))
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
    tracking_one = db.create_order_lifecycle_tracking(
        order_ref_no="SO-2001",
        distributor_id=9,
        order_received_date="2026-01-01",
        expected_delivery_date="2026-01-03",
    )
    tracking_two = db.create_order_lifecycle_tracking(
        order_ref_no="SO-2002",
        distributor_id=9,
        order_received_date="2026-01-10",
        expected_delivery_date="2026-01-12",
    )

    db.record_delivery_receipt(
        tracking_id=tracking_one,
        article_id=article_id,
        invoiced_qty=20,
        physically_received_qty=20,
        damaged_qty=0,
    )
    db.record_delivery_receipt(
        tracking_id=tracking_two,
        article_id=article_id,
        invoiced_qty=30,
        physically_received_qty=30,
        damaged_qty=0,
    )

    logs = db.build_distributor_purchase_behavior_logs(distributor_id=9)

    assert len(logs) == 1
    assert logs[0]["order_count"] == 2
    assert logs[0]["total_volume"] == 50.0
    assert logs[0]["avg_order_interval_days"] == 9.0

from pathlib import Path

from centralized_db_system.db import CentralizedDB


def test_distributor_visit_module_isolated_and_seeded(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "distributor_visits.sqlite3"))

    fields = db.list_distributor_form_fields()
    assert any(field["field_id"] == "current_stock_audit" for field in fields)
    assert any(
        field["field_id"] == "payment_outstanding_credit_limit" for field in fields
    )

    validation = db.validate_distributor_visit_payload(
        {
            "current_stock_audit": "Good",
            "payment_outstanding_credit_limit": "Discussed",
            "new_primary_order_booking": "10 cases",
            "distributor_market_feedback_grievances": "No issue",
            "general_meeting_notes_next_actions": "Follow-up next week",
        }
    )
    assert validation["valid"] is True

    visit_id = db.add_distributor_visit_log(
        distributor_id=101,
        visit_date="2026-06-26",
        visit_time="10:15",
        responses={"current_stock_audit": "Good"},
    )
    assert visit_id > 0


def test_retailer_visit_module_isolated_and_seeded(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "retailer_visits.sqlite3"))

    fields = db.list_retailer_form_fields()
    assert any(field["field_id"] == "secondary_sales_volume" for field in fields)
    assert any(field["field_id"] == "counter_photo_reference" for field in fields)

    validation = db.validate_retailer_visit_payload({"secondary_sales_volume": "25"})
    assert validation["valid"] is False
    assert any("required" in error.lower() for error in validation["errors"])

    visit_id = db.add_retailer_visit_log(
        retailer_id=201,
        linked_distributor_id=101,
        visit_date="2026-06-26",
        visit_time="12:30",
        responses={"secondary_sales_volume": "25"},
    )
    assert visit_id > 0

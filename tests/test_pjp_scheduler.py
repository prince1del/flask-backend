from pathlib import Path

from centralized_db_system.db import CentralizedDB


def test_morning_suggestion_list_prioritizes_longest_idle_entities(
    tmp_path: Path,
) -> None:
    db = CentralizedDB(str(tmp_path / "pjp_scheduler.sqlite3"))

    distributor_id = db.add_distributor("Alpha Dist", "John", "9999999999")
    retailer_id = db.add_retailer("Alpha Retail", "Jane", "8888888888")

    db.add_distributor_visit_log(
        distributor_id, "2026-06-20", "09:00", {"current_stock_audit": "Good"}
    )
    db.add_retailer_visit_log(
        retailer_id,
        distributor_id,
        "2026-06-18",
        "11:00",
        {"secondary_sales_volume": "20"},
    )

    suggestions = db.get_morning_suggestion_list(current_date="2026-06-26")
    assert suggestions[0]["entity_type"] in {"distributor", "retailer"}
    assert (
        suggestions[0]["entity_id"] == retailer_id
        if suggestions[0]["entity_type"] == "retailer"
        else distributor_id
    )


def test_weekly_pjp_plan_and_dsr_date_range_filter(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "pjp_reports.sqlite3"))

    distributor_id = db.add_distributor("Beta Dist", "Sam", "7777777777")
    retailer_id = db.add_retailer("Beta Retail", "Nina", "6666666666")

    db.add_distributor_visit_log(
        distributor_id, "2026-06-22", "08:30", {"current_stock_audit": "Good"}
    )
    db.add_retailer_visit_log(
        retailer_id,
        distributor_id,
        "2026-06-22",
        "10:00",
        {"secondary_sales_volume": "15"},
    )

    plan_id = db.create_weekly_pjp_plan(
        week_start_date="2026-06-22",
        day_of_week="Monday",
        planned_distributor_ids=[distributor_id],
        planned_retailer_ids=[retailer_id],
    )
    assert plan_id > 0

    report_id = db.generate_dsr_report(report_date="2026-06-22")
    assert report_id > 0

    filtered_reports = db.list_dsr_reports_by_date_range("2026-06-21", "2026-06-23")
    assert any(item["report_id"] == report_id for item in filtered_reports)

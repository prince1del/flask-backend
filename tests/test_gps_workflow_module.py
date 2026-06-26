import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from centralized_db_system.db import CentralizedDB


def test_pjp_plan_generates_workflow_todos_and_validates_gps_visit(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "gps_workflow.sqlite3"))

    distributor_id = db.add_master_distributor(
        name="Alpha Traders",
        gst_no="27AAAAA0000A1Z5",
        zone="West",
        region="Pune",
        latitude=18.5204,
        longitude=73.8567,
    )
    plan_id = db.create_weekly_pjp_plan(
        "2026-06-26",
        "Monday",
        [distributor_id],
        [],
    )

    tasks = db.list_workflow_todos_for_party(party_id=distributor_id, party_type="distributor")
    assert plan_id > 0
    assert any(task["task_description"] == "Stock Audit" for task in tasks)

    visit_id = db.add_distributor_visit_log(
        distributor_id=distributor_id,
        visit_date="2026-06-26",
        visit_time="10:00",
        responses={"notes": "Visit started"},
    )
    gps_log_id = db.record_gps_visit_verification(
        visit_log_id=visit_id,
        captured_latitude=18.52041,
        captured_longitude=73.85671,
        device_timestamp=datetime.now(timezone.utc).isoformat(),
        expected_latitude=18.5204,
        expected_longitude=73.8567,
        radius_meters=100,
    )

    assert gps_log_id > 0
    verification = db.get_gps_verification_log(gps_log_id)
    assert verification["geofenced_status"] == "MATCHED"


def test_retention_policy_purges_data_older_than_365_days(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "gps_retention.sqlite3"))

    old_date = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")
    recent_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")

    old_task_id = db.create_workflow_todo_task(
        staff_id=1,
        party_id=1,
        party_type="distributor",
        task_description="Legacy Task",
        created_date=old_date,
    )
    recent_task_id = db.create_workflow_todo_task(
        staff_id=1,
        party_id=1,
        party_type="distributor",
        task_description="Recent Task",
        created_date=recent_date,
    )

    visit_id = db.add_distributor_visit_log(distributor_id=11, visit_date=old_date, responses={"notes": "old visit"})
    db.record_gps_visit_verification(
        visit_log_id=visit_id,
        captured_latitude=18.52,
        captured_longitude=73.85,
        device_timestamp=(datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),
        expected_latitude=18.52,
        expected_longitude=73.85,
        radius_meters=100,
    )

    db.save_verification_output("legacy", "legacy-1", "old report")
    db.save_verification_output("recent", "recent-1", "recent report")

    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE verification_outputs SET created_at = ? WHERE reference_id = ?", ((datetime.now(timezone.utc) - timedelta(days=400)).isoformat(), "legacy-1"))
        conn.commit()

    removed = db.run_retention_policy(retention_days=365)

    assert removed["workflow_todos_deleted"] >= 1
    assert removed["gps_logs_deleted"] >= 1
    assert removed["verification_outputs_deleted"] >= 1
    assert db.get_workflow_todo_task(old_task_id) is None
    assert db.get_workflow_todo_task(recent_task_id) is not None

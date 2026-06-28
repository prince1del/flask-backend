import json
from pathlib import Path

from app.web_app import create_app
from centralized_db_system.db import CentralizedDB


def test_ai_assistant_query_parses_visit_and_alert_questions(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "assistant.sqlite3"))
    distributor_id = db.add_master_distributor(
        name="Prince Enterprises",
        gst_no="27AAAAA0000A1Z5",
        zone="North",
        region="Mumbai",
    )
    db.add_distributor_visit_log(
        distributor_id=1, visit_date="2026-06-20", responses={"notes": "Follow up"}
    )
    db.process_data_entry(
        "Order Sheet",
        {
            "order_ref_no": "ORD-2001",
            "quantity": 10,
            "rate": 100,
            "amount": 900,
            "filled_qty": 8,
        },
        existing_entries=[{"order_ref_no": "ORD-2001"}],
    )

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.post(
        "/api/v1/ai-assistant/query",
        data=json.dumps({"query": "When was the last visit to Prince Enterprises?"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["intent"] == "last_visit"
    assert payload["answer"]

    alert_response = client.post(
        "/api/v1/ai-assistant/query",
        data=json.dumps(
            {"query": "Are there any price mismatches in today's invoice?"}
        ),
        content_type="application/json",
    )
    assert alert_response.status_code == 200
    alert_payload = alert_response.get_json()
    assert alert_payload["intent"] == "alerts"


def test_ai_assistant_query_resolves_pjp_and_purchase_trends(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "assistant_pjp.sqlite3"))
    db.create_weekly_pjp_plan("2026-06-26", "Monday", [1], [2])
    db.add_master_distributor(
        name="Alpha Traders", gst_no="27BBBBB0000A1Z5", zone="West", region="Pune"
    )
    db.add_master_retailer(name="Shop One", distributor_id=1, location="Andheri")
    db.build_distributor_purchase_behavior_logs(1)

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.post(
        "/api/v1/ai-assistant/query",
        data=json.dumps({"query": "Which retailers should I visit today?"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["intent"] == "pjp"

    trend_response = client.post(
        "/api/v1/ai-assistant/query",
        data=json.dumps(
            {"query": "What is the top-selling design for Alpha Traders this month?"}
        ),
        content_type="application/json",
    )
    assert trend_response.status_code == 200
    trend_payload = trend_response.get_json()
    assert trend_payload["intent"] == "purchase_trends"


def test_ai_assistant_query_handles_google_assistant_deep_link_payload(
    tmp_path: Path,
) -> None:
    db = CentralizedDB(str(tmp_path / "assistant_deeplink.sqlite3"))
    db.process_data_entry(
        "Order Sheet",
        {
            "order_ref_no": "ORD-3001",
            "quantity": 10,
            "rate": 100,
            "amount": 900,
            "filled_qty": 8,
        },
        existing_entries=[{"order_ref_no": "ORD-3001"}],
    )

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.get(
        "/api/v1/ai-assistant/query?queryText=ask%20Jarvis%20for%20today%27s%20mismatches"
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["intent"] == "alerts"
    assert payload["answer"].startswith("Jarvis at your service, Boss")

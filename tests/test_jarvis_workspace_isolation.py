"""Ask Nexora / morning-suggestion workspace isolation."""

from centralized_db_system.db import CentralizedDB


def test_morning_suggestions_are_workspace_scoped(tmp_path):
    db = CentralizedDB(str(tmp_path / "jarvis_ws.sqlite3"))
    db.add_master_distributor(name="Mine Dist", buyer_code="M1", workspace_id="ws-mine")
    db.add_master_distributor(name="Other Dist", buyer_code="O1", workspace_id="ws-other")

    mine = db.get_morning_suggestion_list("2026-08-10", workspace_id="ws-mine")
    other = db.get_morning_suggestion_list("2026-08-10", workspace_id="ws-other")

    mine_ids = {row["entity_id"] for row in mine if row["entity_type"] == "distributor"}
    other_ids = {row["entity_id"] for row in other if row["entity_type"] == "distributor"}
    assert mine_ids.isdisjoint(other_ids)
    assert len(mine_ids) >= 1
    assert len(other_ids) >= 1


def test_list_data_entry_alerts_workspace_scoped(tmp_path):
    db = CentralizedDB(str(tmp_path / "alerts_ws.sqlite3"))
    db.create_data_entry_alert(
        document_type="SO",
        reference_no="A1",
        payload={},
        warnings=["w"],
        severity="high",
        workspace_id="ws-a",
    )
    db.create_data_entry_alert(
        document_type="SO",
        reference_no="B1",
        payload={},
        warnings=["w"],
        severity="high",
        workspace_id="ws-b",
    )
    a = db.list_data_entry_alerts(workspace_id="ws-a")
    b = db.list_data_entry_alerts(workspace_id="ws-b")
    assert len(a) == 1 and a[0]["reference_no"] == "A1"
    assert len(b) == 1 and b[0]["reference_no"] == "B1"

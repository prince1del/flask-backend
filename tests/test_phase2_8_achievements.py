from centralized_db_system.db import CentralizedDB


def test_create_and_list_achievement(tmp_path):
    db_path = str(tmp_path / "ach.db")
    db = CentralizedDB(db_path)

    # Create a fake order lifecycle tracking id
    tracking_id = 12345
    ach_id = db.create_achievement(order_lifecycle_tracking_id=tracking_id, amount=15000.0, currency="INR", source="ci", created_by="tester", workspace_id="default", notes="CI verified")
    assert isinstance(ach_id, int)

    items = db.list_achievements(tracking_id=tracking_id)
    assert len(items) == 1
    assert items[0]["amount"] == 15000.0
    assert items[0]["source"] == "ci"

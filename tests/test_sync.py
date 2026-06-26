from centralized_db_system.db import CentralizedDB
from centralized_db_system.sync import OfflineSyncStore, apply_pending_changes


def test_offline_queue_and_replay(tmp_path):
    db_path = tmp_path / "records.sqlite3"
    db = CentralizedDB(str(db_path))
    sync_store = OfflineSyncStore(base_dir=str(tmp_path / "sync-state"))

    record_id = db.add_record("Grace Hopper", "grace@example.com", "Engineering")
    sync_store.enqueue("add", {
        "name": "Grace Hopper",
        "email": "grace@example.com",
        "department": "Engineering",
        "created_at": db.get_record(record_id)["created_at"],
    })

    assert sync_store.pending_count() == 1

    applied = apply_pending_changes(str(db_path), sync_store)
    assert applied == 1
    assert sync_store.pending_count() == 0
    assert db.count_records() == 2

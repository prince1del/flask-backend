from centralized_db_system.db import CentralizedDB
from centralized_db_system.sync import OfflineSyncStore


def test_db_enqueues_sync_when_firebase_unavailable(tmp_path):
    sync_store = OfflineSyncStore(base_dir=str(tmp_path / "sync-state"))
    db = CentralizedDB(str(tmp_path / "records.sqlite3"), sync_store=sync_store)

    db.add_record("Test User", "test@example.com", "Ops")

    assert sync_store.pending_count() >= 1

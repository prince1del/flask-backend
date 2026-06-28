from centralized_db_system.firebase_sync import FirebaseSync
from centralized_db_system.sync import OfflineSyncStore


def test_firebase_sync_queues_when_client_unavailable(tmp_path):
    sync_store = OfflineSyncStore(base_dir=str(tmp_path / "sync-state"))
    firebase_sync = FirebaseSync(
        project_id="demo",
        database_url="https://demo.firebaseio.com",
        sync_store=sync_store,
    )

    firebase_sync.push_record({"name": "Demo", "email": "demo@example.com"})

    assert sync_store.pending_count() == 1

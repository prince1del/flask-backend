import os
from pathlib import Path
from typing import Any

try:
    import firebase_admin
    from firebase_admin import credentials, db
except ImportError:  # pragma: no cover
    firebase_admin = None
    credentials = None
    db = None

from .sync import OfflineSyncStore


class FirebaseSync:
    """Minimal Firebase integration for syncing records to a cloud database."""

    def __init__(self, project_id: str | None = None, database_url: str | None = None, sync_store: OfflineSyncStore | None = None):
        self.project_id = project_id or os.getenv("FIREBASE_PROJECT_ID")
        self.database_url = database_url or os.getenv("FIREBASE_DATABASE_URL")
        self.sync_store = sync_store or OfflineSyncStore()
        self._client = None
        self._initialize_client()

    def _looks_like_real_firebase_config(self) -> bool:
        if not self.database_url:
            return False
        if self.project_id in {None, "", "demo", "test", "example"}:
            return False
        lowered = self.database_url.lower()
        return lowered.startswith("https://") and (".firebaseio.com" in lowered or ".firebasedatabase.app" in lowered)

    def _initialize_client(self) -> None:
        self._client = None
        if firebase_admin is None or credentials is None or db is None:
            return
        if not self._looks_like_real_firebase_config():
            return

        try:
            credential_path = os.getenv("FIREBASE_CREDENTIALS_JSON")
            if not credential_path:
                raise RuntimeError("FIREBASE_CREDENTIALS_JSON is not set")

            credential_file = Path(credential_path).expanduser()
            if not credential_file.exists():
                raise FileNotFoundError(credential_file)

            cred = credentials.Certificate(str(credential_file))
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred, {"databaseURL": self.database_url})

            self._client = db
        except Exception:
            self._client = None

    def push_record(self, record: dict[str, Any]) -> None:
        if self._client is None:
            self.sync_store.enqueue("firebase-add", record)
            return
        try:
            ref = self._client.reference("records")
            ref.push(record)
        except Exception:
            self.sync_store.enqueue("firebase-add", record)

    def sync_pending(self) -> int:
        pending = self.sync_store.dequeue()
        for item in pending:
            if item.get("action") == "firebase-add":
                self.push_record(item.get("payload", {}))
        return len(pending)

    def pull_records(self) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        try:
            ref = self._client.reference("records")
            snapshot = ref.get()
            if isinstance(snapshot, dict):
                return [
                    {"id": key, **value}
                    for key, value in snapshot.items()
                ]
        except Exception:
            return []
        return []

    def get_sync_status(self) -> dict[str, Any]:
        return {
            "configured": self._looks_like_real_firebase_config(),
            "active": self._client is not None,
            "pending_items": len(self.sync_store.peek()),
        }

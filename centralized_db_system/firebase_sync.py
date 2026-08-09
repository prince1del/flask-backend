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

# CentralizedDB() is constructed per request — never spam stdout on every hit.
_FIREBASE_MISSING_LOGGED = False


class FirebaseSync:
    """Minimal Firebase integration for syncing records to a cloud database."""

    def __init__(
        self,
        project_id: str | None = None,
        database_url: str | None = None,
        sync_store: OfflineSyncStore | None = None,
    ):
        # Hum direct aapke sales-manager ka settings ya fallback environment se uthayenge
        self.project_id = (
            project_id or os.getenv("FIREBASE_PROJECT_ID") or "sales-manager-8286d"
        )
        self.database_url = (
            database_url
            or os.getenv("FIREBASE_DATABASE_URL")
            or "https://sales-manager-8286d-default-rtdb.firebaseio.com/"
        )
        self.sync_store = sync_store or OfflineSyncStore()
        self._client = None
        self._initialize_client()

    def _looks_like_real_firebase_config(self) -> bool:
        if not self.database_url:
            return False
        lowered = self.database_url.lower()
        return lowered.startswith("https://") and (
            ".firebaseio.com" in lowered or ".firebasedatabase.app" in lowered
        )

    def _initialize_client(self) -> None:
        global _FIREBASE_MISSING_LOGGED
        self._client = None
        if firebase_admin is None or credentials is None or db is None:
            if not _FIREBASE_MISSING_LOGGED:
                _FIREBASE_MISSING_LOGGED = True
                print("--- Firebase Admin Libraries Missing! (once; optional, not used for Party Master) ---")
            return
        if not self._looks_like_real_firebase_config():
            if not _FIREBASE_MISSING_LOGGED:
                _FIREBASE_MISSING_LOGGED = True
                print("--- Invalid Firebase URL Configuration ---")
            return

        try:
            # .env se chabi ka rasta dhoondhna, nahi toh standard instance/ wala folder fallback lena
            credential_path = (
                os.getenv("FIREBASE_CREDENTIALS_JSON")
                or os.getenv("FIREBASE_CONFIG_PATH")
                or "instance/firebase_credentials.json"
            )

            credential_file = Path(credential_path).expanduser()
            if not credential_file.exists():
                print(
                    f"--- Warning: Firebase JSON chabi nahi mili is raste par: {credential_file} ---"
                )
                return

            cred = credentials.Certificate(str(credential_file))
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred, {"databaseURL": self.database_url})

            self._client = db
            print("--- Firebase Realtime Database Client Started Successfully! ---")
        except Exception as e:
            print(f"--- Firebase Client Init Error: {e} ---")
            self._client = None

    def push_record(self, record: dict[str, Any]) -> None:
        if self._client is None:
            self.sync_store.enqueue("firebase-add", record)
            return
        try:
            # Records collection mein data push hoga
            ref = self._client.reference("records")
            ref.push(record)
            print("--- Record pushed to Firebase successfully! ---")
        except Exception:
            self.sync_store.enqueue("firebase-add", record)

    def sync_verification_status(self, status_data: dict[str, Any]) -> None:
        """Yeh naya function humne jodd diya hai taki web_app ka live stage data Firebase par jaye"""
        if self._client is None:
            return
        try:
            ref = self._client.reference("verification_status")
            ref.set(status_data)
            print("--- Verification Stage Synced to Firebase! ---")
        except Exception as e:
            print(f"--- Stage Sync Failed: {e} ---")

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
                return [{"id": key, **value} for key, value in snapshot.items()]
        except Exception:
            return []
        return []

    def get_sync_status(self) -> dict[str, Any]:
        return {
            "configured": self._looks_like_real_firebase_config(),
            "active": self._client is not None,
            "pending_items": len(self.sync_store.peek()),
        }

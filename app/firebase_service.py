import os
from datetime import datetime, timezone
from typing import Any

from centralized_db_system.firebase_sync import FirebaseSync


class FirebaseEntityService:
    def __init__(self):
        self.sync = FirebaseSync()

    def _push(self, node: str, payload: dict[str, Any]) -> None:
        if self.sync._client is None:
            self.sync.sync_store.enqueue("firebase-add", {"node": node, **payload})
            return
        self.sync._client.reference(node).push(payload)

    def add_distributor(self, payload: dict[str, Any]) -> str:
        payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        self._push("distributors", payload)
        return payload["created_at"]

    def add_retailer(self, payload: dict[str, Any]) -> str:
        payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        self._push("retailers", payload)
        return payload["created_at"]

    def add_product(self, payload: dict[str, Any]) -> str:
        payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        self._push("products", payload)
        return payload["created_at"]

    def add_stock_transfer(self, payload: dict[str, Any]) -> str:
        payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        self._push("stockTransfers", payload)
        return payload["created_at"]

    def add_payment(self, payload: dict[str, Any]) -> str:
        payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        self._push("payments", payload)
        return payload["created_at"]

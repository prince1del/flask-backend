import json
import os
import sqlite3
from pathlib import Path
from typing import Any


class OfflineSyncStore:
    """Queue local changes and replay them when a remote backend becomes available."""

    def __init__(self, base_dir: str | None = None):
        self.base_dir = Path(
            base_dir or os.getenv("SYNC_STATE_DIR", "./sync-state")
        ).expanduser()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.queue_path = self.base_dir / "pending.json"
        self.queue_path.touch(exist_ok=True)

    def enqueue(self, action: str, payload: dict[str, Any]) -> None:
        pending = self._read_queue()
        pending.append({"action": action, "payload": payload})
        self._write_queue(pending)

    def peek(self) -> list[dict[str, Any]]:
        return self._read_queue()

    def dequeue(self) -> list[dict[str, Any]]:
        pending = self._read_queue()
        self._write_queue([])
        return pending

    def pending_count(self) -> int:
        return len(self._read_queue())

    def _read_queue(self) -> list[dict[str, Any]]:
        if not self.queue_path.exists():
            return []
        try:
            data = json.loads(self.queue_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def _write_queue(self, items: list[dict[str, Any]]) -> None:
        self.queue_path.write_text(json.dumps(items, indent=2), encoding="utf-8")


def apply_pending_changes(db_path: str, sync_store: OfflineSyncStore) -> int:
    """Replay queued operations against the database."""
    pending = sync_store.dequeue()
    if not pending:
        return 0

    with sqlite3.connect(db_path) as conn:
        for item in pending:
            action = item.get("action")
            payload = item.get("payload", {})
            if action == "add":
                conn.execute(
                    "INSERT INTO records (name, email, department, created_at) VALUES (?, ?, ?, ?)",
                    (
                        payload.get("name"),
                        payload.get("email"),
                        payload.get("department"),
                        payload.get("created_at"),
                    ),
                )
            elif action == "update":
                conn.execute(
                    f"UPDATE records SET {payload['field']} = ? WHERE id = ?",
                    (payload.get("value"), payload.get("record_id")),
                )
            elif action == "delete":
                conn.execute(
                    "DELETE FROM records WHERE id = ?", (payload.get("record_id"),)
                )
        conn.commit()
    return len(pending)

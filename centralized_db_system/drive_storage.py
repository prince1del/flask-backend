from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class GoogleDriveStorage:
    """A lightweight Google Drive-backed storage wrapper for large media files."""

    def __init__(self, folder_id: str | None = None, client: Any | None = None) -> None:
        self.folder_id = folder_id or os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
        self.client = client

    def upload_file(
        self, path: str | Path, file_name: str | None = None
    ) -> dict[str, Any]:
        path = Path(path)
        name = file_name or path.name
        file_id = f"drive-{abs(hash(path.name))}"
        return {
            "file_id": file_id,
            "name": name,
            "size": path.stat().st_size if path.exists() else 0,
            "storage": "google_drive",
            "folder_id": self.folder_id,
        }

    def download_file(self, file_id: str) -> bytes:
        if self.client is None:
            return b""
        return self.client.download(file_id)

    def download_local_file(self, path: str | Path) -> bytes:
        return Path(path).read_bytes() if Path(path).exists() else b""

    def get_public_url(self, file_id: str) -> str:
        return f"https://drive.google.com/file/d/{file_id}/view"

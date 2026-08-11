"""Push Nexora order PDFs into the user's Drive/NEXORA folder."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def push_pdf_to_nexora_drive(
    *,
    user_id: int | None,
    workspace_id: str | None,
    local_path: str | Path | None,
    subfolder: str,
    display_name: str | None = None,
) -> dict[str, Any] | None:
    """Upload a local PDF into NEXORA/<subfolder>. Returns Drive metadata or None."""
    if not user_id or not local_path:
        return None
    path = Path(str(local_path))
    if not path.is_file():
        return None
    try:
        from app.storage.manager import StorageManager
        from app.storage.providers.google_drive_provider import GoogleDriveProvider

        manager = StorageManager()
        manager.register_provider("google_drive", GoogleDriveProvider)
        provider = manager._get_user_provider(int(user_id), workspace_id=workspace_id)
        workspace = provider.ensure_nexora_workspace()
        folder_id = workspace["folders"].get(subfolder) or workspace["root_id"]
        uploaded = provider.upload(
            str(path),
            folder_id,
            display_name=display_name or path.name,
        )
        return uploaded
    except KeyError:
        return None
    except Exception:
        logger.exception("NEXORA Drive upload failed for %s", local_path)
        return None

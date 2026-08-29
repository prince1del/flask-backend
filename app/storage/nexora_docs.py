"""Push Nexora order documents into the user's Drive/NEXORA folder.

Local upload storage lives on an ephemeral disk that is wiped on every
redeploy, so Drive is the only copy of an uploaded document that lasts.
Every stage of the order chain belongs here — the distributor's Filled
Order workbook, the company's Sales Order, and the Commercial Invoice —
otherwise the trail cannot be reconstructed later.

Uploads are always best-effort: a Drive outage, or Drive simply not being
connected, must never fail the upload the user is doing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def push_file_to_nexora_drive(
    *,
    user_id: int | None,
    workspace_id: str | None,
    local_path: str | Path | None,
    subfolder: str,
    display_name: str | None = None,
    replace_if_exists: bool = False,
) -> dict[str, Any] | None:
    """Upload any local file into NEXORA/<subfolder>. Returns Drive metadata or None.

    Not PDF-specific: Drive detects the type from the file itself, so
    workbooks (.xlsx) upload exactly the same way.

    replace_if_exists: when True, overwrite a same-named file in that subfolder
    instead of creating a duplicate (used for rolling JSON snapshots).
    """
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
        provider = manager._get_user_provider(
            int(user_id), workspace_id=workspace_id, provider_type="google_drive"
        )
        workspace = provider.ensure_nexora_workspace()
        folder_id = workspace["folders"].get(subfolder) or workspace["root_id"]
        upload_fn = provider.upload_or_replace if replace_if_exists else provider.upload
        uploaded = upload_fn(
            str(path),
            folder_id,
            display_name=display_name or path.name,
        )
        return uploaded
    except KeyError as exc:
        # Was a bare `return None`. Google Drive not being connected is a
        # normal state, but so is "connected, yet every backup silently does
        # nothing" — which is exactly what happened when a leftover 'gmail'
        # storage account shadowed the Drive one. Say which, either way.
        logger.warning(
            "NEXORA Drive backup skipped for %s — no usable Google Drive "
            "account for user %s in workspace %r (%s)",
            local_path, user_id, workspace_id, exc,
        )
        return None
    except Exception:
        logger.exception("NEXORA Drive upload failed for %s", local_path)
        return None


def remove_file_from_nexora_drive(
    *,
    user_id: int | None,
    workspace_id: str | None,
    subfolder: str,
    display_name: str,
) -> bool:
    """Remove a same-named file from NEXORA/<subfolder> (best-effort).

    Used when an old SO Pack backup stored the whole zip — re-upload should
    leave only unpacked PDFs in Sales Orders.
    """
    name = (display_name or "").strip()
    if not user_id or not name:
        return False
    try:
        from app.storage.manager import StorageManager
        from app.storage.providers.google_drive_provider import GoogleDriveProvider

        manager = StorageManager()
        manager.register_provider("google_drive", GoogleDriveProvider)
        provider = manager._get_user_provider(
            int(user_id), workspace_id=workspace_id, provider_type="google_drive"
        )
        workspace = provider.ensure_nexora_workspace()
        folder_id = workspace["folders"].get(subfolder) or workspace["root_id"]
        existing_id = provider._find_file_by_name(name, folder_id)
        if not existing_id:
            return False
        provider.delete(existing_id)
        return True
    except Exception:
        logger.exception("NEXORA Drive delete failed for %s", name)
        return False


# Original name, kept so existing SO/CI callers keep working unchanged.
push_pdf_to_nexora_drive = push_file_to_nexora_drive

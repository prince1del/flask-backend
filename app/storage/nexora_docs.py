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


def _index_drive_upload(
    *,
    user_id: int,
    storage_account_id: int | None,
    workspace_id: str | None,
    folder_id: str | None,
    uploaded: dict[str, Any],
) -> None:
    """Keep file_index in sync so Settings → Drive shows new backups immediately."""
    if not storage_account_id or not uploaded.get("id"):
        return
    try:
        from centralized_db_system.db import CentralizedDB

        item = dict(uploaded)
        if folder_id and not item.get("parents"):
            item["parents"] = [folder_id]
        CentralizedDB().upsert_file_index_item(
            str(workspace_id or "default"),
            int(storage_account_id),
            item,
            user_id=user_id,
        )
    except Exception:
        logger.exception(
            "Drive file index update failed for user %s file %s",
            user_id,
            uploaded.get("id"),
        )


def push_file_to_nexora_drive(
    *,
    user_id: int | None,
    workspace_id: str | None,
    local_path: str | Path | None,
    subfolder: str,
    display_name: str | None = None,
    replace_if_exists: bool = False,
    season: str | None = None,
    category: str | None = None,
    distributor_name: str | None = None,
) -> dict[str, Any] | None:
    """Upload into NEXORA/{subfolder}/{Season}/{Category}/{Distributor}/file."""
    if not user_id or not local_path:
        return None
    path = Path(str(local_path))
    if not path.is_file():
        return None
    try:
        from app.storage.manager import StorageManager
        from app.storage.nexora_drive_paths import build_order_desk_drive_segments
        from app.storage.providers.google_drive_provider import GoogleDriveProvider

        manager = StorageManager()
        manager.register_provider("google_drive", GoogleDriveProvider)
        provider = manager._get_user_provider(
            int(user_id), workspace_id=workspace_id, provider_type="google_drive"
        )
        connection = getattr(manager, "user_connections", {}).get(int(user_id)) or {}
        workspace = provider.ensure_nexora_workspace()
        base_id = workspace["folders"].get(subfolder) or workspace["root_id"]
        segments: list[str] = []
        if season or category or distributor_name:
            segments = build_order_desk_drive_segments(
                season=season,
                category=category,
                distributor_name=distributor_name,
            )
        folder_id = (
            provider.ensure_folder_path(base_id, *segments)
            if segments
            else base_id
        )
        upload_fn = provider.upload_or_replace if replace_if_exists else provider.upload
        uploaded = upload_fn(
            str(path),
            folder_id,
            display_name=display_name or path.name,
        )
        if uploaded and uploaded.get("id"):
            _index_drive_upload(
                user_id=int(user_id),
                storage_account_id=connection.get("storage_account_id"),
                workspace_id=connection.get("workspace_id") or workspace_id,
                folder_id=folder_id,
                uploaded=uploaded,
            )
            logger.info(
                "NEXORA Drive backup OK user=%s path=%s/%s file=%s id=%s",
                user_id,
                subfolder,
                "/".join(segments),
                display_name or path.name,
                uploaded.get("id"),
            )
        return uploaded
    except KeyError as exc:
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

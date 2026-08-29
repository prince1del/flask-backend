from __future__ import annotations

import json
from typing import Any

from centralized_db_system.db import CentralizedDB

from app.storage.provider import StorageProvider


class StorageManager:
    """Orchestrates all storage operations."""

    def __init__(self) -> None:
        self.providers: dict[str, type[StorageProvider] | StorageProvider] = {}
        self.user_connections: dict[int, dict[str, Any]] = {}
        self.db = CentralizedDB()
        self.db.ensure_storage_tables()

    def register_provider(
        self,
        provider_type: str,
        provider_instance: type[StorageProvider] | StorageProvider,
    ) -> None:
        """Register new storage provider (Google Drive, OneDrive, etc.)."""
        self.providers[provider_type] = provider_instance

    def connect_user_storage(
        self, user_id: int, provider_type: str, oauth_token: Any, workspace_id: str = 'default'
    ) -> dict[str, Any]:
        """Connect user's cloud storage account."""
        provider_class = self.providers.get(provider_type)
        if provider_class is None:
            raise KeyError(f"Unknown provider type: {provider_type}")

        if isinstance(provider_class, StorageProvider):
            provider = provider_class
        else:
            provider = provider_class(oauth_token)

        storage_account_id = self.db.save_storage_account(
            user_id=user_id,
            workspace_id=workspace_id,
            provider_type=provider_type,
            oauth_token=oauth_token,
            sync_status="connected",
        )
        self.user_connections[user_id] = {
            "provider_type": provider_type,
            "oauth_token": oauth_token,
            "provider": provider,
            "storage_account_id": storage_account_id,
            "workspace_id": workspace_id,
        }
        return {
            "user_id": user_id,
            "provider_type": provider_type,
            "storage_account_id": storage_account_id,
            "workspace_id": workspace_id,
            "connected": True,
        }

    def disconnect_user_storage(self, user_id: int, workspace_id: str = 'default') -> dict[str, Any]:
        """Disconnect user's cloud storage."""
        connection = self.user_connections.pop(user_id, None)
        stored_account = self.db.get_storage_account(user_id, workspace_id=workspace_id)
        if connection is None and stored_account is None:
            return {
                "user_id": user_id,
                "disconnected": False,
                "reason": "not_connected",
            }

        provider_type = (
            connection["provider_type"]
            if connection
            else stored_account["provider_type"]
        )
        self.db.disconnect_storage_account(user_id, workspace_id=workspace_id, provider_type=provider_type)
        return {"user_id": user_id, "disconnected": True}

    def _get_persisted_connection(
        self, user_id: int, workspace_id: str | None = None,
        provider_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Load the connected account, optionally pinned to one provider.

        storage_accounts is UNIQUE(user_id, workspace_id, provider_type), so a
        user can hold several rows at once — a Google Drive account and, from
        the Gmail import that once existed, a 'gmail' one. Asking without a
        provider_type returns whichever row comes back first: if that was the
        gmail row, self.providers has no 'gmail' entry, this raised
        KeyError("Unknown provider type: gmail"), and every Drive upload
        silently did nothing while the app still reported Drive as connected.
        Callers that want Drive must say so.
        """
        account = self.db.get_storage_account(
            user_id, provider_type=provider_type, workspace_id=workspace_id
        )
        if account is None:
            return None

        found_type = account["provider_type"]
        provider_class = self.providers.get(found_type)
        if provider_class is None:
            raise KeyError(f"Unknown provider type: {found_type}")
        provider_type = found_type

        if isinstance(provider_class, StorageProvider):
            provider = provider_class
        else:
            provider = provider_class(account["oauth_token"])

        connection = {
            "provider_type": provider_type,
            "oauth_token": account["oauth_token"],
            "provider": provider,
            "storage_account_id": account["id"],
            "workspace_id": account.get("workspace_id"),
        }
        self.user_connections[user_id] = connection
        return connection

    def _get_user_provider(
        self, user_id: int, workspace_id: str | None = None,
        provider_type: str | None = None,
    ) -> StorageProvider:
        connection = self.user_connections.get(user_id)
        # The cache is keyed by user alone, so it can hold a connection for a
        # different workspace — or a different provider than the caller wants.
        # Both must miss, or one feature's account gets served to another.
        if connection is None or (
            workspace_id and connection.get("workspace_id") != workspace_id
        ) or (
            provider_type and connection.get("provider_type") != provider_type
        ):
            connection = self._get_persisted_connection(
                user_id, workspace_id, provider_type=provider_type
            )
            if connection:
                self.user_connections[user_id] = connection
        if not connection:
            raise KeyError("No storage provider connected for user")
        provider = connection.get("provider")
        if not isinstance(provider, StorageProvider):
            raise TypeError("Connected provider is invalid")
        return provider

    def upload_file(
        self, user_id: int, file_path: str, company: str, module: str, folder: str
    ) -> dict[str, Any]:
        """Upload file through storage manager."""
        provider = self._get_user_provider(user_id)
        return provider.upload(file_path=file_path, target_folder=folder)

    def download_file(
        self, user_id: int, file_id: str, target_path: str, workspace_id: str | None = None
    ) -> dict[str, Any]:
        """Download file through storage manager."""
        provider = self._get_user_provider(user_id, workspace_id=workspace_id)
        return provider.download(file_id=file_id, target_path=target_path)

    def download_file_bytes(
        self, user_id: int, file_id: str, workspace_id: str | None = None
    ) -> dict[str, Any]:
        """Download file bytes through the connected Drive account (not browser Google session)."""
        provider = self._get_user_provider(user_id, workspace_id=workspace_id)
        if hasattr(provider, "download_bytes"):
            return provider.download_bytes(file_id)
        # Fallback for providers without in-memory download
        import tempfile
        import os

        fd, path = tempfile.mkstemp(prefix="nexora_drive_")
        os.close(fd)
        try:
            provider.download(file_id=file_id, target_path=path)
            with open(path, "rb") as fh:
                content = fh.read()
            return {
                "file_id": file_id,
                "file_name": file_id,
                "mime_type": "application/octet-stream",
                "content": content,
            }
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def list_files(self, user_id: int, folder_path: str = "") -> list[dict[str, Any]]:
        """List files for a connected storage provider."""
        provider = self._get_user_provider(user_id)
        return provider.list_files(folder_path)

    def sync_user_storage(
        self,
        user_id: int,
        incremental: bool = True,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Sync user's storage with metadata DB."""
        connection = self.user_connections.get(user_id)
        if connection is None or (
            workspace_id and connection.get("workspace_id") != workspace_id
        ):
            connection = self._get_persisted_connection(user_id, workspace_id)
        if not connection:
            raise KeyError("No storage provider connected for user")

        provider = connection["provider"]
        resolved_workspace = (
            workspace_id or connection.get("workspace_id") or "default"
        )
        items = provider.sync(incremental=incremental)
        storage_account_id = connection.get("storage_account_id")
        synced = 0
        if storage_account_id:
            synced = self.db.upsert_file_index_records(
                resolved_workspace,
                storage_account_id,
                items,
                user_id=user_id,
            )
        return {
            "user_id": user_id,
            "synced_items": synced,
            "files_found": len(items),
            "workspace_id": resolved_workspace,
        }

    def search_files(
        self, user_id: int, workspace_id: str, query: str, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Search indexed files."""
        return self.db.search_file_index(user_id, workspace_id, query, filters)

    def get_storage_account(self, user_id: int, workspace_id: str) -> dict[str, Any] | None:
        """Return persisted storage account metadata for the user."""
        account = self.db.get_storage_account(user_id, workspace_id=workspace_id)
        if not account:
            return None
        return {
            "id": account["id"],
            "provider_type": account["provider_type"],
            "connected_at": account["connected_at"],
            "last_sync": account["last_sync"],
            "sync_status": account["sync_status"],
            "total_storage_bytes": account["total_storage_bytes"],
            "used_storage_bytes": account["used_storage_bytes"],
            "workspace_id": account.get("workspace_id"),
        }

    def get_storage_dashboard(self, user_id: int, workspace_id: str) -> dict[str, Any]:
        """Get storage usage dashboard."""
        connection = self.user_connections.get(
            user_id
        ) or self._get_persisted_connection(user_id)
        if not connection:
            return {"user_id": user_id, "storage_info": {}, "connected": False}

        provider = connection["provider"]
        storage_info = provider.get_storage_info()
        summary = self.db.get_storage_account_summary(user_id, workspace_id=workspace_id) or {}
        summary["storage_info"] = storage_info
        summary["connected"] = True
        return summary

import io
import json
import os
from typing import Any

from app.storage.provider import StorageProvider


class GoogleDriveProvider(StorageProvider):
    """Google Drive Storage Provider Implementation."""

    SCOPES = ["https://www.googleapis.com/auth/drive"]
    NEXORA_FOLDER_NAME = "NEXORA"
    NEXORA_SUBFOLDERS = (
        "Downloads",
        "Catalogues",
        # One folder per stage of the order chain, so the whole trail is
        # kept somewhere durable: the distributor's Filled Order, the
        # company's Sales Order against it, and the Commercial Invoice
        # against that. Local upload storage is on an ephemeral disk that
        # is wiped on every redeploy — Drive is the only copy that lasts.
        "Filled Orders",
        "Order Sheets",
        "Sales Orders",
        "Commercial Invoices",
        "Invoices",
        "Reports",
        "Backups",
        "Payment Receiving",
    )

    def __init__(self, oauth_token: Any):
        self.service = self.authenticate(oauth_token)
        self.folder_cache: dict[str, str] = {}
        self.nexora_root_id: str | None = None

    def authenticate(self, oauth_token: Any):
        """Authenticate with Google Drive API."""
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError(
                "google-auth and google-api-python-client are required for Google Drive integration"
            ) from exc

        if isinstance(oauth_token, str):
            oauth_token = json.loads(oauth_token)
        if not isinstance(oauth_token, dict):
            raise TypeError("oauth_token must be a dict or JSON string")

        creds = Credentials.from_authorized_user_info(oauth_token, self.SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

        return build("drive", "v3", credentials=creds)

    def upload(
        self, file_path: str, target_folder: str, display_name: str | None = None
    ) -> dict[str, Any]:
        """Upload file to Google Drive."""
        metadata = {"name": display_name or os.path.basename(file_path)}
        parent = target_folder
        if not parent:
            workspace = self.ensure_nexora_workspace()
            parent = workspace["folders"].get("Downloads") or workspace["root_id"]
        if parent:
            metadata["parents"] = [parent]
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(file_path, resumable=True)
        try:
            file = (
                self.service.files()
                .create(
                    body=metadata,
                    media_body=media,
                    fields="id,name,mimeType,size,modifiedTime",
                )
                .execute()
            )
            return file
        except Exception as exc:
            raise RuntimeError(f"Google Drive upload failed: {exc}") from exc

    def download(self, file_id: str, target_path: str) -> dict[str, Any]:
        """Download file from Google Drive."""
        payload = self.download_bytes(file_id)
        with open(target_path, "wb") as fh:
            fh.write(payload["content"])
        return {
            "file_id": file_id,
            "target_path": target_path,
            "file_name": payload.get("file_name"),
            "mime_type": payload.get("mime_type"),
        }

    def download_bytes(self, file_id: str) -> dict[str, Any]:
        """Download file bytes (exports Google Docs/Sheets/Slides to Office formats)."""
        try:
            from googleapiclient.http import MediaIoBaseDownload
        except ImportError as exc:
            raise RuntimeError(
                "google-api-python-client is required for Google Drive downloads"
            ) from exc

        meta = (
            self.service.files()
            .get(fileId=file_id, fields="id,name,mimeType,size")
            .execute()
        )
        mime = str(meta.get("mimeType") or "")
        name = str(meta.get("name") or file_id)

        if mime == "application/vnd.google-apps.folder":
            raise RuntimeError("Folders cannot be downloaded. Open a file instead.")

        export_map = {
            "application/vnd.google-apps.document": (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".docx",
            ),
            "application/vnd.google-apps.spreadsheet": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".xlsx",
            ),
            "application/vnd.google-apps.presentation": (
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ".pptx",
            ),
            "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
        }

        buffer = io.BytesIO()
        if mime in export_map:
            export_mime, ext = export_map[mime]
            request = self.service.files().export_media(fileId=file_id, mimeType=export_mime)
            if not name.lower().endswith(ext):
                name = f"{name}{ext}"
            out_mime = export_mime
        elif mime.startswith("application/vnd.google-apps."):
            raise RuntimeError(
                f"This Google Drive item type cannot be downloaded in NEXORA ({mime})."
            )
        else:
            request = self.service.files().get_media(fileId=file_id)
            out_mime = mime or "application/octet-stream"

        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _status, done = downloader.next_chunk()

        return {
            "file_id": file_id,
            "file_name": name,
            "mime_type": out_mime,
            "content": buffer.getvalue(),
        }

    def delete(self, file_id: str) -> dict[str, Any]:
        """Delete file from Google Drive."""
        try:
            from googleapiclient.errors import HttpError
        except ImportError:
            HttpError = Exception  # type: ignore
        try:
            self.service.files().delete(fileId=file_id).execute()
            return {"file_id": file_id, "deleted": True}
        except HttpError as exc:
            raise RuntimeError(f"Google Drive delete failed: {exc}") from exc

    def _escape_drive_query(self, value: str) -> str:
        return (value or "").replace("\\", "\\\\").replace("'", "\\'")

    def _find_child_folder(self, name: str, parent_id: str) -> str | None:
        q = (
            f"name = '{self._escape_drive_query(name)}' and "
            f"mimeType = 'application/vnd.google-apps.folder' and "
            f"'{parent_id}' in parents and trashed = false"
        )
        response = (
            self.service.files()
            .list(q=q, pageSize=1, fields="files(id,name)")
            .execute()
        )
        files = response.get("files") or []
        return str(files[0]["id"]) if files else None

    def _find_file_by_name(self, name: str, parent_id: str) -> str | None:
        q = (
            f"name = '{self._escape_drive_query(name)}' and "
            f"mimeType != 'application/vnd.google-apps.folder' and "
            f"'{parent_id}' in parents and trashed = false"
        )
        response = (
            self.service.files()
            .list(q=q, pageSize=1, fields="files(id,name)", orderBy="modifiedTime desc")
            .execute()
        )
        files = response.get("files") or []
        return str(files[0]["id"]) if files else None

    def upload_or_replace(
        self, file_path: str, target_folder: str, display_name: str | None = None
    ) -> dict[str, Any]:
        """Upload a file, replacing same-named file in the folder if it already exists."""
        name = display_name or os.path.basename(file_path)
        parent = target_folder
        if not parent:
            workspace = self.ensure_nexora_workspace()
            parent = workspace["folders"].get("Downloads") or workspace["root_id"]
        existing_id = self._find_file_by_name(name, parent) if parent else None
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(file_path, resumable=True)
        try:
            if existing_id:
                file = (
                    self.service.files()
                    .update(
                        fileId=existing_id,
                        body={"name": name},
                        media_body=media,
                        fields="id,name,mimeType,size,modifiedTime",
                    )
                    .execute()
                )
                return file
            metadata: dict[str, Any] = {"name": name}
            if parent:
                metadata["parents"] = [parent]
            file = (
                self.service.files()
                .create(
                    body=metadata,
                    media_body=media,
                    fields="id,name,mimeType,size,modifiedTime",
                )
                .execute()
            )
            return file
        except Exception as exc:
            raise RuntimeError(f"Google Drive upload_or_replace failed: {exc}") from exc

    def ensure_folder_path(self, parent_id: str, *segment_names: str) -> str:
        """Create or find nested folders: parent/seg1/seg2/… Returns deepest id."""
        current = parent_id
        for raw_name in segment_names:
            name = (raw_name or "").strip()
            if not name:
                continue
            cache_key = f"{current}|{name}"
            cached = self.folder_cache.get(cache_key)
            if cached:
                current = cached
                continue
            child_id = self._find_child_folder(name, current)
            if not child_id:
                created = self.create_folder(name, parent_folder=current)
                child_id = str(created["id"])
            self.folder_cache[cache_key] = child_id
            current = child_id
        return current

    def ensure_nexora_workspace(self) -> dict[str, Any]:
        """Create Drive/NEXORA with Downloads, Invoices, Reports, Backups if missing."""
        if self.nexora_root_id and self.nexora_root_id in self.folder_cache:
            root_id = self.nexora_root_id
        else:
            root_id = self._find_child_folder(self.NEXORA_FOLDER_NAME, "root")
            if not root_id:
                created = self.create_folder(self.NEXORA_FOLDER_NAME, parent_folder="root")
                root_id = str(created["id"])
            self.nexora_root_id = root_id
            self.folder_cache[root_id] = self.NEXORA_FOLDER_NAME

        folders: dict[str, str] = {}
        for name in self.NEXORA_SUBFOLDERS:
            child_id = self._find_child_folder(name, root_id)
            if not child_id:
                created = self.create_folder(name, parent_folder=root_id)
                child_id = str(created["id"])
            folders[name] = child_id
            self.folder_cache[child_id] = name
        return {"root_id": root_id, "name": self.NEXORA_FOLDER_NAME, "folders": folders}

    def list_nexora_tree(self) -> list[dict[str, Any]]:
        """All files and folders under Drive/NEXORA (not the rest of the user's Drive)."""
        workspace = self.ensure_nexora_workspace()
        collected: list[dict[str, Any]] = []
        queue = [workspace["root_id"]]
        seen: set[str] = set()
        while queue:
            parent_id = queue.pop()
            if parent_id in seen:
                continue
            seen.add(parent_id)
            for item in self.list_files(parent_id):
                collected.append(item)
                mime = str(item.get("mimeType") or "")
                child_id = str(item.get("id") or "")
                if child_id and "folder" in mime.lower():
                    queue.append(child_id)
        return collected

    def list_files(self, folder_path: str) -> list[dict[str, Any]]:
        """List files in a Google Drive folder. Empty path = NEXORA workspace only."""
        if not folder_path:
            return self.list_nexora_tree()
        query = f"'{folder_path}' in parents and trashed = false"
        try:
            files: list[dict[str, Any]] = []
            page_token = None
            while True:
                response = (
                    self.service.files()
                    .list(
                        q=query,
                        pageSize=200,
                        pageToken=page_token,
                        fields="nextPageToken, files(id,name,mimeType,modifiedTime,size,parents)",
                        orderBy="modifiedTime desc",
                    )
                    .execute()
                )
                files.extend(response.get("files", []))
                page_token = response.get("nextPageToken")
                if not page_token or len(files) >= 2000:
                    break
            return files
        except Exception as exc:
            raise RuntimeError(f"Google Drive list_files failed: {exc}") from exc

    def create_folder(
        self, folder_name: str, parent_folder: str | None = None
    ) -> dict[str, Any]:
        """Create folder in Google Drive."""
        metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_folder:
            metadata["parents"] = [parent_folder]
        try:
            folder = (
                self.service.files().create(body=metadata, fields="id,name").execute()
            )
            return folder
        except Exception as exc:
            raise RuntimeError(f"Google Drive create_folder failed: {exc}") from exc

    def move(self, file_id: str, target_folder: str) -> dict[str, Any]:
        """Move file to another folder."""
        file = self.service.files().get(fileId=file_id, fields="parents").execute()
        previous_parents = ",".join(file.get("parents", []))
        try:
            updated = (
                self.service.files()
                .update(
                    fileId=file_id,
                    addParents=target_folder,
                    removeParents=previous_parents,
                    fields="id,parents",
                )
                .execute()
            )
            return updated
        except Exception as exc:
            raise RuntimeError(f"Google Drive move failed: {exc}") from exc

    def copy(self, file_id: str, target_folder: str) -> dict[str, Any]:
        """Copy file to another folder."""
        try:
            copied = (
                self.service.files()
                .copy(fileId=file_id, body={"parents": [target_folder]})
                .execute()
            )
            return copied
        except Exception as exc:
            raise RuntimeError(f"Google Drive copy failed: {exc}") from exc

    def rename(self, file_id: str, new_name: str) -> dict[str, Any]:
        """Rename file."""
        try:
            renamed = (
                self.service.files()
                .update(fileId=file_id, body={"name": new_name}, fields="id,name")
                .execute()
            )
            return renamed
        except Exception as exc:
            raise RuntimeError(f"Google Drive rename failed: {exc}") from exc

    def sync(self, incremental: bool = True) -> list[dict[str, Any]]:
        """Sync only the NEXORA Drive folder into the metadata DB."""
        return self.list_nexora_tree()

    def get_file_metadata(self, file_id: str) -> dict[str, Any]:
        """Get file metadata from Google Drive."""
        try:
            metadata = (
                self.service.files()
                .get(
                    fileId=file_id, fields="id,name,mimeType,modifiedTime,size,parents"
                )
                .execute()
            )
            return metadata
        except Exception as exc:
            raise RuntimeError(f"Google Drive get_file_metadata failed: {exc}") from exc

    def get_storage_info(self) -> dict[str, Any]:
        """Get Google Drive storage usage."""
        try:
            about = self.service.about().get(fields="storageQuota").execute()
            return about.get("storageQuota", {})
        except Exception as exc:
            raise RuntimeError(f"Google Drive get_storage_info failed: {exc}") from exc

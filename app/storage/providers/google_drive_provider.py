import io
import json
import os
from typing import Any

from app.storage.provider import StorageProvider


class GoogleDriveProvider(StorageProvider):
    """Google Drive Storage Provider Implementation."""

    SCOPES = ["https://www.googleapis.com/auth/drive"]

    def __init__(self, oauth_token: Any):
        self.service = self.authenticate(oauth_token)
        self.folder_cache: dict[str, str] = {}

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

    def upload(self, file_path: str, target_folder: str) -> dict[str, Any]:
        """Upload file to Google Drive."""
        metadata = {"name": os.path.basename(file_path)}
        if target_folder:
            metadata["parents"] = [target_folder]
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

    def list_files(self, folder_path: str) -> list[dict[str, Any]]:
        """List files in Google Drive folder (paginated)."""
        query = "trashed = false"
        if folder_path:
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
        """Sync Google Drive with metadata DB."""
        return self.list_files(folder_path="")

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

import io
import os
import logging

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.models import User
from app.encryption import CredentialEncryption

logger = logging.getLogger(__name__)


class UserGDriveService:
    """Google Drive service for a specific user."""

    def __init__(self, user_id: int):
        self.user = User.query.get(user_id)
        if not self.user or not self.user.gdrive_connected:
            raise Exception(f"User {user_id} has not connected Google Drive")

        encryption = CredentialEncryption()
        access_token = encryption.decrypt(self.user.gdrive_access_token)
        refresh_token = (
            encryption.decrypt(self.user.gdrive_refresh_token)
            if self.user.gdrive_refresh_token
            else None
        )

        self.creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
            scopes=["https://www.googleapis.com/auth/drive"],
        )

        if self.creds.expired and self.creds.refresh_token:
            self.creds.refresh(Request())

        self.drive_service = build("drive", "v3", credentials=self.creds)
        self.nexora_folder_id = None
        self.invoices_folder_id = None
        self.reports_folder_id = None
        self.backups_folder_id = None

    def upload_invoice(self, invoice_id: str, pdf_bytes: bytes) -> dict[str, str]:
        """Upload invoice PDF to user's Google Drive."""
        self._ensure_folder_structure()

        file_metadata = {
            "name": f"Invoice-{invoice_id}.pdf",
            "parents": [self.invoices_folder_id],
        }

        media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf")
        file = (
            self.drive_service.files()
            .create(body=file_metadata, media_body=media, fields="id,webViewLink")
            .execute()
        )

        logger.info(
            f"User {self.user.id} uploaded invoice {invoice_id} to GDrive: {file.get('id')}"
        )
        return {"file_id": file.get("id"), "drive_url": file.get("webViewLink")}

    def _ensure_folder_structure(self) -> None:
        results = self.drive_service.files().list(
            q="name='NEXORA' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            pageSize=1,
            fields='files(id)',
        ).execute()
        files = results.get("files", [])
        if files:
            self.nexora_folder_id = files[0]["id"]
        else:
            folder = self.drive_service.files().create(
                body={
                    "name": "NEXORA",
                    "mimeType": "application/vnd.google-apps.folder",
                }
            ).execute()
            self.nexora_folder_id = folder["id"]

        self._ensure_subfolder("Invoices")
        self._ensure_subfolder("Reports")
        self._ensure_subfolder("Backups")

    def _ensure_subfolder(self, subfolder_name: str) -> None:
        results = self.drive_service.files().list(
            q=f"name='{subfolder_name}' and '{self.nexora_folder_id}' in parents and trashed=false",
            pageSize=1,
            fields='files(id)',
        ).execute()
        files = results.get("files", [])
        folder_id = files[0]["id"] if files else None

        if folder_id:
            setattr(self, f"{subfolder_name.lower()}_folder_id", folder_id)
            return

        subfolder = self.drive_service.files().create(
            body={
                "name": subfolder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [self.nexora_folder_id],
            }
        ).execute()
        setattr(self, f"{subfolder_name.lower()}_folder_id", subfolder["id"])

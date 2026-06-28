from abc import ABC, abstractmethod
from typing import Any


class StorageProvider(ABC):
    """Abstract Storage Provider Interface"""

    @abstractmethod
    def upload(self, file_path: str, target_folder: str) -> dict[str, Any]:
        """Upload file to storage provider."""
        raise NotImplementedError

    @abstractmethod
    def download(self, file_id: str, target_path: str) -> dict[str, Any]:
        """Download file from storage provider."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, file_id: str) -> dict[str, Any]:
        """Delete file from storage provider."""
        raise NotImplementedError

    @abstractmethod
    def list_files(self, folder_path: str) -> list[dict[str, Any]]:
        """List files in folder."""
        raise NotImplementedError

    @abstractmethod
    def create_folder(self, folder_name: str, parent_folder: str | None = None) -> dict[str, Any]:
        """Create folder in storage."""
        raise NotImplementedError

    @abstractmethod
    def move(self, file_id: str, target_folder: str) -> dict[str, Any]:
        """Move file to another folder."""
        raise NotImplementedError

    @abstractmethod
    def copy(self, file_id: str, target_folder: str) -> dict[str, Any]:
        """Copy file to another folder."""
        raise NotImplementedError

    @abstractmethod
    def rename(self, file_id: str, new_name: str) -> dict[str, Any]:
        """Rename file."""
        raise NotImplementedError

    @abstractmethod
    def sync(self, incremental: bool = True) -> list[dict[str, Any]]:
        """Sync files from storage provider."""
        raise NotImplementedError

    @abstractmethod
    def get_file_metadata(self, file_id: str) -> dict[str, Any]:
        """Get file metadata."""
        raise NotImplementedError

    @abstractmethod
    def get_storage_info(self) -> dict[str, Any]:
        """Get storage usage info."""
        raise NotImplementedError

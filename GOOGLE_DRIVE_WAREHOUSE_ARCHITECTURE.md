# Google Drive Warehouse Architecture (v1.1)

## 1. Overview

This document defines the v1.1 architecture for Google Drive Warehouse, a storage integration layer for NEXORA. The design is provider-agnostic, with a first implementation for Google Drive.

## 2. Storage Provider Interface (Abstract)

The `StorageProvider` interface defines a common contract for all storage providers.

```python
class StorageProvider:
    def upload(self, file, path):
        raise NotImplementedError

    def download(self, file_id):
        raise NotImplementedError

    def delete(self, file_id):
        raise NotImplementedError

    def list_files(self, folder):
        raise NotImplementedError

    def create_folder(self, name):
        raise NotImplementedError

    def sync(self, incremental=True):
        raise NotImplementedError

    def get_file_metadata(self, file_id):
        raise NotImplementedError
```

### Responsibilities

- Define a clean storage abstraction.
- Hide provider-specific API details behind a common interface.
- Enable future providers to plug into the same orchestrator.

## 3. Storage Manager (Orchestrator)

The `StorageManager` routes requests to the correct provider and manages multi-account and metadata sync behavior.

### Responsibilities

- Route upload/download/delete/list/sync calls to the correct provider instance.
- Manage multiple connected storage accounts per user or organization.
- Coordinate metadata sync and indexing.
- Cache provider status and connection metadata.
- Provide a central API for storage-related operations.

### Key functions

- `register_provider(provider_type, provider_class)`
- `connect_account(user_id, provider_type, oauth_data)`
- `get_provider(account_id)`
- `upload_file(account_id, file, path)`
- `download_file(account_id, file_id)`
- `delete_file(account_id, file_id)`
- `list_files(account_id, folder)`
- `sync_account(account_id, incremental=True)`
- `get_file_metadata(account_id, file_id)`

## 4. Google Drive Provider v1 (Implementation)

### Responsibilities

- Implement `StorageProvider` for Google Drive.
- Handle OAuth integration and token refresh.
- Map common storage operations to Google Drive API calls.
- Implement provider-specific error handling.

### Features

- Google OAuth 2.0 flow for user account authorization.
- Drive API wrappers for upload, download, delete, list, folder creation, and metadata lookup.
- Token refresh and revocation support.
- Scoped permission handling.
- Support for incremental sync through Drive change tracking.

### Error handling

- Map Google API response codes to internal exceptions.
- Handle rate limits, expired tokens, permission revocation, and network failures.
- Surface retryable and non-retryable errors.

## 5. File Index Schema

The file index stores metadata for files discovered or uploaded through the storage layer.

```sql
CREATE TABLE file_index (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id TEXT NOT NULL,
  file_name TEXT NOT NULL,
  folder_path TEXT,
  owner TEXT,
  company TEXT,
  module TEXT,
  upload_date TIMESTAMP,
  modified_date TIMESTAMP,
  size INTEGER,
  mime_type TEXT,
  search_tags TEXT,
  processing_status TEXT,
  indexed_at TIMESTAMP
);

CREATE TABLE storage_accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  provider_type TEXT NOT NULL,
  oauth_token TEXT,
  refresh_token TEXT,
  connected_at TIMESTAMP,
  total_storage INTEGER,
  used_storage INTEGER
);
```

### Indexes

- `CREATE INDEX idx_file_index_file_name ON file_index(file_name);`
- `CREATE INDEX idx_file_index_folder_path ON file_index(folder_path);`
- `CREATE INDEX idx_file_index_search_tags ON file_index(search_tags);`
- `CREATE INDEX idx_storage_accounts_user_id ON storage_accounts(user_id);`

## 6. Three File Modes

### Mode 1: Upload through NEXORA → Drive → Index

- User uploads a file in NEXORA UI.
- Upload Manager sends file to Google Drive provider.
- Metadata is created and inserted into `file_index`.
- File is available for search and download.

### Mode 2: Manual Drive upload → Sync discovers → Index

- User uploads directly into Google Drive.
- Sync Engine discovers new or changed files.
- Index entries are created or updated.
- Files become available in the NEXORA file library.

### Mode 3: AI Processing → Read from Drive → Extract → Discard temp copy

- AI Processing engine reads file content from Drive.
- Temporary copies are used for processing only.
- Extracted metadata or results are stored.
- Temporary local copies are deleted after processing.

## 7. Sync Engine Design

The Sync Engine keeps Drive metadata aligned with the local file index.

### Sync modes

- Manual Sync (on-demand): triggered by user request.
- Automatic Sync (scheduled): periodic background sync.
- Background Sync (continuous): event-driven or polling.
- Delta sync: only changed files since the last sync.

### Responsibilities

- Query provider for changed files.
- Align provider metadata with `file_index`.
- Detect additions, modifications, and deletions.
- Update sync status and last sync timestamp.

### Sync flow

1. Determine last sync checkpoint.
2. Fetch changed files from provider.
3. Update/insert index records.
4. Remove deleted files from index.
5. Record sync summary and errors.

## 8. Upload Manager

The Upload Manager handles uploads from NEXORA to Google Drive.

### Responsibilities

- Accept file payloads from API.
- Validate target path and folder.
- Upload file to provider.
- Create file metadata record in `file_index`.
- Return upload status and metadata.

### Flow

1. Receive `POST /api/v1/files/upload`.
2. Validate account and path.
3. Upload file through `GoogleDriveProvider.upload()`.
4. Create indexed metadata entry.
5. Return response with file info.

## 9. Download Manager

The Download Manager handles retrieving files from Drive.

### Responsibilities

- Lookup indexed metadata.
- Download file from provider.
- Perform temporary processing if needed.
- Stream file to user.
- Cleanup temporary artifacts.

### Flow

1. Receive `GET /api/v1/files/{file_id}/download`.
2. Lookup file metadata in `file_index`.
3. Download file via provider.
4. Optionally process/convert.
5. Return file response and cleanup.

## 10. File Library UI Flow

### Browse indexed files

- Display indexed files with metadata.
- Show folder path, owner, upload date, size, and status.

### Search by tags/name/date

- Provide search filters for file name, tags, date range, owner, and module.

### Preview metadata

- Display file metadata details before download.

### Download

- Provide a download action per file.

### Delete

- Provide a delete action for indexed files and the provider file where permitted.

## 11. Storage Dashboard UI

### Dashboard sections

- Connected provider accounts
- Total / used / free storage
- Indexed file count
- Last sync time
- Sync status
- Largest files
- Recent uploads

### Metadata

- Provider type
- Account owner
- Connected at
- Available storage metrics

## 12. Security & OAuth

### Google OAuth 2.0 flow

- Use OAuth authorization code flow.
- Request minimum required Drive scopes.
- Persist refresh token securely.
- Support account disconnect.

### Token storage

- Store OAuth tokens encrypted at rest.
- Keep refresh tokens separate from user session data.
- Rotate or revoke tokens on disconnect.

### Permissions

- Request least privilege scopes.
- Support read, write, metadata, and folder access as needed.

### Audit log

- Track connect/disconnect events.
- Track uploads, downloads, syncs, and errors.
- Include user and account context.

## 13. Future Provider Roadmap

- OneDrive Provider
- Dropbox Provider
- AWS S3 Provider
- Azure Blob Storage
- SharePoint Provider

## 14. Implementation Roadmap

### Phase 1: Design
- Complete architecture design documentation.
- Validate schema and interface.

### Phase 2: Storage Manager + Interface
- Build `StorageProvider` abstract class.
- Implement `StorageManager` orchestration layer.

### Phase 3: Google Drive Provider v1
- Implement Google Drive provider.
- Handle OAuth and Drive API.

### Phase 4: File Index tables
- Create `file_index` and `storage_accounts` tables.
- Add indexes for search.

### Phase 5: Sync engine
- Implement manual, automatic, and delta sync.
- Build sync status tracking.

### Phase 6: Upload/Download managers
- Add upload and download endpoints.
- Implement metadata creation and cleanup.

### Phase 7: File Library UI
- Build browsing and search UI.
- Add preview and download flows.

### Phase 8: Storage Dashboard UI
- Build provider account dashboard.
- Add storage and sync metrics.

### Phase 9: Testing + deployment
- Unit tests, integration tests, and deployment verification.
- Deploy to Render.

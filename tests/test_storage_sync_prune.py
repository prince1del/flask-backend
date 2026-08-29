"""Drive index sync must drop files deleted on Google Drive."""

import sqlite3
from datetime import datetime, timezone

from centralized_db_system.db import CentralizedDB


def _seed_storage_account(db: CentralizedDB) -> int:
    db.ensure_storage_tables()
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            """
            INSERT INTO storage_accounts (
                user_id, workspace_id, provider_type, oauth_token, sync_status, connected_at, updated_at
            ) VALUES (1, 'ws', 'google_drive', '{}', 'connected', ?, ?)
            """,
            (now, now),
        )
        row = conn.execute("SELECT id FROM storage_accounts WHERE user_id = 1").fetchone()
        conn.commit()
        return int(row[0])


def test_upsert_file_index_prunes_deleted_drive_files(tmp_path):
    db = CentralizedDB(db_path=str(tmp_path / "nexora.db"))
    account_id = _seed_storage_account(db)

    items = [
        {"id": "pdf-1", "name": "BND 102876560.pdf", "mimeType": "application/pdf", "size": 100},
        {"id": "zip-old", "name": "bnd.zip", "mimeType": "application/zip", "size": 200},
    ]
    result = db.upsert_file_index_records("ws", account_id, items, user_id=1)
    assert result["upserted"] == 2

    # User deleted bnd.zip on Drive — next sync only returns the PDF.
    result2 = db.upsert_file_index_records(
        "ws",
        account_id,
        [{"id": "pdf-1", "name": "BND 102876560.pdf", "mimeType": "application/pdf", "size": 100}],
        user_id=1,
    )
    assert result2["removed"] == 1

    with sqlite3.connect(db.db_path) as conn:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT file_name FROM file_index WHERE storage_account_id = ? ORDER BY file_name",
                (account_id,),
            ).fetchall()
        ]
    assert names == ["BND 102876560.pdf"]

import os
from pathlib import Path

from .firebase_sync import FirebaseSync


def run_smoke_test() -> str:
    project_id = os.getenv("FIREBASE_PROJECT_ID", "")
    database_url = os.getenv("FIREBASE_DATABASE_URL", "")
    credential_path = os.getenv("FIREBASE_CREDENTIALS_JSON", "")

    if not project_id or not database_url or not credential_path:
        return "Firebase is not fully configured. Set FIREBASE_PROJECT_ID, FIREBASE_DATABASE_URL, and FIREBASE_CREDENTIALS_JSON."

    if not Path(credential_path).exists():
        return f"Credential file not found: {credential_path}"

    sync = FirebaseSync(project_id=project_id, database_url=database_url)
    if sync._client is None:
        return "Firebase client could not be initialized. Check your credentials and database URL."

    return "Firebase configuration looks valid."

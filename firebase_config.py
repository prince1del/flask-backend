import os


FIREBASE_CONFIG = {
    "project_id": os.getenv("FIREBASE_PROJECT_ID", "your-project-id"),
    "api_key": os.getenv("FIREBASE_API_KEY", "your-api-key"),
    "auth_domain": os.getenv("FIREBASE_AUTH_DOMAIN", "your-project-id.firebaseapp.com"),
    "database_url": os.getenv("FIREBASE_DATABASE_URL", "https://your-project-id.firebaseio.com"),
    "storage_bucket": os.getenv("FIREBASE_STORAGE_BUCKET", "your-project-id.firebasestorage.app"),
}

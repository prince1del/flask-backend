import os
import sqlite3
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash


def ensure_users(db_path: str | None = None) -> list[dict[str, object]]:
    db_path = db_path or os.getenv("DATABASE_PATH", "centralized_db.sqlite3")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    existing_columns = {
        row[1] for row in cur.execute("PRAGMA table_info(users)").fetchall()
    }
    for column_name, column_sql in (("role", "TEXT"), ("workspace_id", "TEXT")):
        if column_name not in existing_columns:
            cur.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_sql}")

    users = [
        ("mobile_test_admin", "mobile_test_admin_123", "admin", "bombay_dyeing"),
        ("mobile_test_user", "mobile_test_user_123", "user", "bombay_dyeing"),
    ]

    created = []
    for username, password, role, workspace_id in users:
        existing = cur.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        password_hash = generate_password_hash(password)
        if existing:
            cur.execute(
                "UPDATE users SET password_hash = ?, role = ?, workspace_id = ? WHERE username = ?",
                (password_hash, role, workspace_id, username),
            )
            created.append({"username": username, "status": "updated"})
        else:
            created_at = datetime.now(timezone.utc).isoformat()
            cur.execute(
                "INSERT INTO users (username, password_hash, role, workspace_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (username, password_hash, role, workspace_id, created_at),
            )
            created.append({"username": username, "status": "created"})

    conn.commit()
    conn.close()
    return created


if __name__ == "__main__":
    for item in ensure_users():
        print(item)

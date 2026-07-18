"""Create the isolated House of Prizm admin user.

Does NOT modify existing users (including NEXORA executive / sales_executive accounts).

Usage:
  .venv\\Scripts\\python.exe scripts\\create_hop_user.py
  .venv\\Scripts\\python.exe scripts\\create_hop_user.py --username hop_prizm --password "YourSecurePass"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env_file(ROOT / ".env")

from app.hop_schema import HOP_ROLE, HOP_WORKSPACE_ID, ensure_hop_schema
from centralized_db_system.db import CentralizedDB


def main() -> int:
    parser = argparse.ArgumentParser(description="Create House of Prizm hop_admin user")
    parser.add_argument("--username", default=os.getenv("HOP_ADMIN_USERNAME", "hop_prizm"))
    parser.add_argument("--password", default=os.getenv("HOP_ADMIN_PASSWORD", "Prizm@2026!"))
    parser.add_argument(
        "--db",
        default=os.getenv("DATABASE_PATH", str(ROOT / "centralized_db.sqlite3")),
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    ensure_hop_schema(db_path)
    db = CentralizedDB(str(db_path))

    try:
        user = db.create_user(
            args.username,
            args.password,
            role=HOP_ROLE,
            workspace_id=HOP_WORKSPACE_ID,
        )
    except ValueError as exc:
        if "already exists" in str(exc).lower():
            print(f"User '{args.username}' already exists — left unchanged (no password reset).")
            print(f"Role expected: {HOP_ROLE} | workspace: {HOP_WORKSPACE_ID}")
            return 0
        raise

    print("House of Prizm user created (existing NEXORA users untouched).")
    print(f"  user_id   : {user['id']}")
    print(f"  username  : {user['username']}")
    print(f"  role      : {HOP_ROLE}")
    print(f"  workspace : {HOP_WORKSPACE_ID}")
    print(f"  password  : {args.password}")
    print("Login with this account to open the House of Prizm executive shell.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

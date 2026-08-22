"""Create or update the isolated House of Prizm admin user.

Does NOT modify existing NEXORA executive / sales_executive accounts unless
that account is the HoP login being renamed (hop_prizm → prince1del).

Usage:
  .venv\\Scripts\\python.exe scripts\\create_hop_user.py
  .venv\\Scripts\\python.exe scripts\\create_hop_user.py --username prince1del --password "YourSecurePass"
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

from centralized_db_system.db import CentralizedDB


def main() -> int:
    parser = argparse.ArgumentParser(description="Create House of Prizm hop_admin user")
    parser.add_argument(
        "--username",
        default=os.getenv("HOP_ADMIN_USERNAME", "prince1del"),
    )
    parser.add_argument(
        "--password",
        default=os.getenv("HOP_ADMIN_PASSWORD", "@Princeking123"),
    )
    parser.add_argument(
        "--old-username",
        default=os.getenv("HOP_ADMIN_OLD_USERNAME", "hop_prizm"),
    )
    parser.add_argument(
        "--db",
        default=os.getenv("DATABASE_PATH", str(ROOT / "centralized_db.sqlite3")),
    )
    args = parser.parse_args()

    db = CentralizedDB(str(Path(args.db)))
    result = db.ensure_hop_admin_login(
        old_username=args.old_username,
        new_username=args.username,
        new_password=args.password,
    )

    print("House of Prizm login ready (existing NEXORA users untouched).")
    print(f"  action    : {result.get('action')}")
    print(f"  user_id   : {result.get('user_id')}")
    print(f"  username  : {args.username}")
    print(f"  password  : {args.password}")
    print("Login with this account to open the House of Prizm executive shell.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Rename legacy BD login on local DB (Render does this on deploy via migrate_bd_owner_login)."""
from centralized_db_system.db import CentralizedDB

print(CentralizedDB("centralized_db.sqlite3").migrate_bd_owner_login())

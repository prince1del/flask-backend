"""One-shot local rename: bd_gt_north_head → kps.julka@gmail.com"""
from centralized_db_system.db import CentralizedDB

db = CentralizedDB("centralized_db.sqlite3")
print(db.migrate_bd_owner_login())

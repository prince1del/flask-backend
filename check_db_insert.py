from centralized_db_system.db import CentralizedDB
from pathlib import Path

p = Path("tmp_test_db.sqlite3")
if p.exists():
    p.unlink()

db = CentralizedDB(str(p))
inserted = db.add_master_distributor(
    name="PDF Distributor TEST", gst_no="27ABCDE1234F1Z5", zone="West", region="Mumbai"
)
print("inserted id:", inserted)
found = db.get_master_distributor_by_name("PDF Distributor TEST")
print("found:", found)

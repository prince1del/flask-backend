from centralized_db_system.db import CentralizedDB

name = "PDF Distributor 94a6400b"
db = CentralizedDB("centralized_db.sqlite3")
print(db.get_master_distributor_by_name(name))

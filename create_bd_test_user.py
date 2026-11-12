from centralized_db_system.db import CentralizedDB

db = CentralizedDB('centralized_db.sqlite3')
result = db.create_user(
    'kps.julka@gmail.com',
    __import__("os").environ["NEXORA_TEST_PASSWORD"],
    role='sales_executive',
    workspace_id='bombay_dyeing_gt_north',
)
print('User successfully bana:', result)

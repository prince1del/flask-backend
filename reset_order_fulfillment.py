import sqlite3
import shutil
from pathlib import Path

DB_PATH = "centralized_db.sqlite3"
WORKSPACE_ID = "bombay_dyeing_gt_north"

conn = sqlite3.connect(DB_PATH)

print("=== Before cleanup ===")
for table in ["order_lifecycle_tracking", "order_fulfillment_items", "order_sheet_master"]:
    try:
        count = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE workspace_id = ?", (WORKSPACE_ID,)
        ).fetchone()[0]
        print(f"  {table}: {count} rows")
    except sqlite3.OperationalError as e:
        print(f"  {table}: ERROR ({e})")

# Delete achievements linked to this workspace's tracking records first
# (foreign-key-style cleanup, in the right order)
conn.execute(
    """
    DELETE FROM achievements WHERE order_lifecycle_tracking_id IN (
        SELECT tracking_id FROM order_lifecycle_tracking WHERE workspace_id = ?
    )
    """,
    (WORKSPACE_ID,),
)
conn.execute(
    """
    DELETE FROM order_fulfillment_items WHERE order_lifecycle_id IN (
        SELECT tracking_id FROM order_lifecycle_tracking WHERE workspace_id = ?
    )
    """,
    (WORKSPACE_ID,),
)
conn.execute("DELETE FROM order_lifecycle_tracking WHERE workspace_id = ?", (WORKSPACE_ID,))
conn.execute("DELETE FROM order_sheet_master WHERE workspace_id = ?", (WORKSPACE_ID,))
conn.commit()

print("\n=== After cleanup (should be 0, 0, 0) ===")
for table in ["order_lifecycle_tracking", "order_fulfillment_items", "order_sheet_master"]:
    count = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE workspace_id = ?", (WORKSPACE_ID,)
    ).fetchone()[0]
    print(f"  {table}: {count} rows")

conn.close()

# Wipe every uploaded file on disk (Order Fulfillment's own folder,
# plus the OLDER, separate /legacy verification-uploads folder).
folders_to_wipe = [
    "app/instance/order_fulfillment_files",
    "instance/order_fulfillment_files",
    "app/instance/verification_uploads",
    "instance/verification_uploads",
]
print("\n=== Wiping uploaded files ===")
for folder in folders_to_wipe:
    path = Path(folder)
    if path.exists():
        file_count = sum(1 for _ in path.rglob("*") if _.is_file())
        shutil.rmtree(path)
        print(f"  Deleted {folder} ({file_count} files)")
    else:
        print(f"  {folder}: did not exist, skipped")

print("\nDone. Order Fulfillment is now a clean slate — Company Profile is untouched (separate table).")

import sqlite3

conn = sqlite3.connect('centralized_db.sqlite3')

WORKSPACE_ID = "bombay_dyeing_gt_north"

print("Before cleanup:")
for table in ["master_distributors", "master_retailers"]:
    count = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE workspace_id = ?", (WORKSPACE_ID,)
    ).fetchone()[0]
    print(f"  {table}: {count} rows for workspace '{WORKSPACE_ID}'")

# No interactive confirmation — this script is meant to be run
# deliberately, one command at a time, only when you actually want a
# clean slate before a fresh upload.
for table in ["master_retailers", "master_distributors"]:
    cursor = conn.execute(f"DELETE FROM {table} WHERE workspace_id = ?", (WORKSPACE_ID,))
    print(f"Deleted {cursor.rowcount} rows from {table}")
conn.commit()
print("\nCleanup complete.")

print("\nAfter cleanup (should be 0, 0):")
for table in ["master_distributors", "master_retailers"]:
    count = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE workspace_id = ?", (WORKSPACE_ID,)
    ).fetchone()[0]
    print(f"  {table}: {count} rows")

conn.close()

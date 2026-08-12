import sqlite3

conn = sqlite3.connect('centralized_db.sqlite3')

WORKSPACE_ID = "bombay_dyeing_gt_north"

print("Before cleanup:")
for table in ["master_distributors", "master_retailers"]:
    count = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE workspace_id = ?", (WORKSPACE_ID,)
    ).fetchone()[0]
    print(f"  {table}: {count} rows for workspace '{WORKSPACE_ID}'")

confirm = input(
    f"\nThis will DELETE all master_distributors and master_retailers rows "
    f"for workspace '{WORKSPACE_ID}' only (other workspaces untouched). "
    f"Type 'yes' to proceed: "
)
if confirm.strip().lower() != "yes":
    print("Aborted — nothing deleted.")
else:
    for table in ["master_retailers", "master_distributors"]:
        cursor = conn.execute(f"DELETE FROM {table} WHERE workspace_id = ?", (WORKSPACE_ID,))
        print(f"Deleted {cursor.rowcount} rows from {table}")
    conn.commit()
    print("\nCleanup complete. You can now do a single, fresh upload of both files.")

conn.close()

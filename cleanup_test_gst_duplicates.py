import sqlite3

conn = sqlite3.connect('centralized_db.sqlite3')

print("Before cleanup:")
for table in ["distributors", "retailers"]:
    rows = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE workspace_id IN ('ws-1', 'ws-2')"
    ).fetchone()
    print(f"  {table}: {rows[0]} test rows found (workspace_id in ws-1/ws-2)")

confirm = input("\nDelete these test rows? Type 'yes' to proceed: ")
if confirm.strip().lower() != "yes":
    print("Aborted — nothing deleted.")
else:
    for table in ["distributors", "retailers"]:
        cursor = conn.execute(
            f"DELETE FROM {table} WHERE workspace_id IN ('ws-1', 'ws-2')"
        )
        print(f"Deleted {cursor.rowcount} rows from {table}")
    conn.commit()
    print("\nCleanup complete.")

conn.close()

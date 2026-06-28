import sqlite3
from pathlib import Path

p = Path("centralized_db.sqlite3").resolve()
print("DB file:", p, "exists:", p.exists())
if not p.exists():
    print("DB not found")
else:
    conn = sqlite3.connect(str(p))
    cur = conn.cursor()
    try:
        rows = cur.execute(
            "SELECT id, name, gst_no, created_at FROM master_distributors ORDER BY id DESC LIMIT 10"
        ).fetchall()
        print("recent distributors:")
        for r in rows:
            print(r)
    except Exception as e:
        print("error querying:", e)
    finally:
        conn.close()

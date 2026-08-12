"""Verify per-user Article Master seed data."""

import json
import sqlite3

DB_PATH = r"E:\centralized-db-system\centralized_db.sqlite3"
SEED_USERNAME = "bd_gt_north_head"

conn = sqlite3.connect(DB_PATH)

row = conn.execute(
    "SELECT id FROM users WHERE username = ?", (SEED_USERNAME,)
).fetchone()
if row is None:
    raise SystemExit(f"User '{SEED_USERNAME}' not found")
USER_ID = row[0]
print(f"Verifying user_id={USER_ID} ({SEED_USERNAME})")

print("\n=== 1. Total count per category ===")
rows = conn.execute(
    "SELECT category, COUNT(*) FROM article_master WHERE user_id = ? GROUP BY category",
    (USER_ID,),
).fetchall()
total = 0
for category, count in rows:
    print(f"  {category}: {count}")
    total += count
print(f"  TOTAL: {total} (expect 153)")

print("\n=== 2. Duplicate item_key check ===")
dupes = conn.execute(
    """SELECT item_key, COUNT(*) c FROM article_master
       WHERE user_id = ? GROUP BY item_key HAVING c > 1""",
    (USER_ID,),
).fetchall()
print(f"  Duplicates: {len(dupes)}" + (" - OK" if not dupes else f" - PROBLEM: {dupes}"))

print("\n=== 3. NULL check on core fields ===")
for field in ["brand", "size", "mrp", "ptr", "ex_mill_price", "bale_pack_size"]:
    count = conn.execute(
        f"SELECT COUNT(*) FROM article_master WHERE user_id = ? AND {field} IS NULL",
        (USER_ID,),
    ).fetchone()[0]
    print(f"  {field}: {count} NULL" + (" - OK" if count == 0 else " - PROBLEM"))

print("\n=== 4. Spot-check known values ===")
checks = [
    ("ASTER|100|DB BS", "mrp", 999),
    ("BLANKET|ALL SEASON BLANKET|150X220", "mrp", 1299),
    ("PILLOW FILLER|NOVA|43X69", "mrp", 699),
]
for item_key, field, expected in checks:
    row = conn.execute(
        f"SELECT {field} FROM article_master WHERE user_id = ? AND item_key = ?",
        (USER_ID, item_key),
    ).fetchone()
    actual = row[0] if row else None
    match = row and abs(float(actual) - expected) < 0.01
    print(f"  {item_key} -> {field}={actual} (expect {expected})" + (" - OK" if match else " - PROBLEM"))

print("\n=== 5. extra_attributes JSON integrity ===")
sample = conn.execute(
    "SELECT item_key, extra_attributes FROM article_master WHERE user_id = ? LIMIT 3",
    (USER_ID,),
).fetchall()
for item_key, extra_raw in sample:
    try:
        parsed = json.loads(extra_raw)
        print(f"  {item_key}: {len(parsed)} extra fields, parses OK")
    except Exception as e:
        print(f"  {item_key}: JSON PARSE FAILED - {e}")

print("\n=== 6. category_master key_fields ===")
cats = conn.execute(
    "SELECT category_name, key_fields, is_confirmed FROM category_master WHERE user_id = ?",
    (USER_ID,),
).fetchall()
for name, kf, confirmed in cats:
    print(f"  {name}: key_fields={kf}, confirmed={bool(confirmed)}")

print("\n=== 7. Per-user isolation (other users should see 0) ===")
other_count = conn.execute(
    "SELECT COUNT(*) FROM article_master WHERE user_id != ?", (USER_ID,)
).fetchone()[0]
print(f"  Articles belonging to other users: {other_count} - OK" if other_count == 0 else f"  PROBLEM: {other_count}")

conn.close()
print("\nVerification complete.")

"""
Article Master — One-time Seed Script

Loads 4 real booking-form Excel files into the per-user Article Master.

Run order:
  1. python fix_article_master_tables.py   (if schema needs rebuild)
  2. python seed_article_master.py
  3. python verify_article_master.py
"""

import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import article_master_db as amdb
from article_master_parser import parse_article_sheet

# ============================================================
# CONFIG
# ============================================================
DB_PATH = r"E:\centralized-db-system\centralized_db.sqlite3"
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "article_master_schema.sql")

# Seed target user — lookup by username; change if needed
SEED_USERNAME = "bd_gt_north_head"
WORKSPACE_ID = "bombay_dyeing_gt_north"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

FILE_KEYWORDS = {
    "Bed": ["bedsheet"],
    "TOB": ["tob"],
    "Pillow": ["pillow"],
    "Bath": ["towel"],
}

CATEGORY_KEY_FIELDS = {
    "Bed": ["brand", "TC", "size"],
    "Bath": ["brand", "size", "color", "product"],
    "TOB": ["brand", "size", "product", "color"],
    "Pillow": ["brand", "size", "product"],
}
# ============================================================


def discover_files(folder):
    found = {}
    for fname in os.listdir(folder):
        if not fname.lower().endswith(".xlsx"):
            continue
        fname_lower = fname.lower()
        for category, keywords in FILE_KEYWORDS.items():
            if any(kw in fname_lower for kw in keywords):
                found[category] = os.path.join(folder, fname)
                break
    return found


def resolve_user_id(conn, username):
    row = conn.execute(
        "SELECT id, workspace_id FROM users WHERE username = ?", (username,)
    ).fetchone()
    if row is None:
        raise SystemExit(
            f"User '{username}' not found. Create with create_bd_test_user.py first."
        )
    return row[0], row[1] or WORKSPACE_ID


def main():
    print(f"Connecting to {DB_PATH} ...")
    conn = sqlite3.connect(DB_PATH)

    user_id, workspace_id = resolve_user_id(conn, SEED_USERNAME)
    print(f"Seeding for user_id={user_id} ({SEED_USERNAME}), workspace_id={workspace_id}")

    print("Creating schema (safe if tables already exist) ...")
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())

    print("\nSeeding categories ...")
    for category_name, key_fields in CATEGORY_KEY_FIELDS.items():
        existing = amdb.get_category_by_name(conn, user_id, category_name)
        if existing:
            print(f"  {category_name}: already exists, skipping")
        else:
            amdb.create_category(
                conn, user_id, category_name, key_fields,
                is_confirmed=True, workspace_id=workspace_id,
            )
            print(f"  {category_name}: created with key_fields={key_fields}")

    print("\nLooking for booking-form files in this folder ...")
    discovered = discover_files(SCRIPT_DIR)
    for category in CATEGORY_KEY_FIELDS:
        if category in discovered:
            print(f"  {category}: found -> {os.path.basename(discovered[category])}")
        else:
            print(f"  {category}: NOT FOUND (keyword {FILE_KEYWORDS[category]})")

    print("\nLoading files ...")
    total_created, total_updated, total_review = 0, 0, 0
    all_review_rows = []

    for expected_category, filepath in discovered.items():
        fname = os.path.basename(filepath)
        sheet_name = pd.ExcelFile(filepath).sheet_names[0]

        articles, category, is_new_category, needs_review, _breakdown = parse_article_sheet(
            filepath, sheet_name, CATEGORY_KEY_FIELDS
        )

        if is_new_category:
            print(f"  {fname}: UNRECOGNIZED CATEGORY - skipped")
            continue

        if category != expected_category:
            print(
                f"  {fname}: WARNING - filename suggested {expected_category} "
                f"but content detected as {category}"
            )

        created, updated = 0, 0
        for article in articles:
            _, was_created, changed_fields = amdb.upsert_article(
                conn, user_id, article,
                source_filename=fname, workspace_id=workspace_id,
            )
            if was_created:
                created += 1
            elif changed_fields:
                updated += 1

        total_created += created
        total_updated += updated
        total_review += len(needs_review)
        all_review_rows.extend([(fname, a) for a in needs_review])
        print(f"  {fname}: category={category}, created={created}, updated={updated}, needs_review={len(needs_review)}")

    print(f"\n{'='*60}")
    print(f"SUMMARY: created={total_created}, updated={total_updated}, needs_review={total_review}")
    print(f"{'='*60}")

    if all_review_rows:
        print("\nRows needing manual review:")
        for fname, row in all_review_rows:
            print(f"  [{fname}] item_key={row['item_key']!r}")

    print("\nVerification - per category counts:")
    for category_name in CATEGORY_KEY_FIELDS:
        articles = amdb.get_articles_by_category(conn, user_id, category_name)
        print(f"  {category_name}: {len(articles)} articles")
        for a in articles[:2]:
            print(f"      {a['brand']} | {a['size']} | MRP={a['mrp']} | item_key={a['item_key']}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()

"""Article Master rows appear in home-screen global search."""

import json
import sqlite3
from pathlib import Path

import article_master_db as amdb
from centralized_db_system.db import CentralizedDB


def _seed_article_master(db_path, user_id=2):
    schema_path = Path(__file__).resolve().parent.parent / "article_master_schema.sql"
    conn = sqlite3.connect(db_path)
    with open(schema_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    amdb.create_category(conn, user_id, "Bed", ["brand", "TC", "size"], is_confirmed=True)
    amdb.ensure_default_brand_aliases(conn, user_id)
    amdb.insert_article(
        conn,
        user_id,
        {
            "category": "Bed",
            "brand": "Bluman",
            "size": "DB BS",
            "product_type": "Sheet Sets",
            "mrp": 1299,
            "ptr": 866,
            "ex_mill_price": 733.9,
            "bale_pack_size": 18,
            "item_key": "BLUMAN|104|DB BS",
            "extra_attributes": {"TC": "104", "Print Style": "Digital"},
        },
    )
    conn.close()


def test_global_search_finds_article_master_by_brand(tmp_path):
    db_path = str(tmp_path / "am_search.sqlite3")
    _seed_article_master(db_path)
    db = CentralizedDB(db_path)

    results = db.global_search("bluman", user_id=2)
    rows = results["results"]["article_master"]
    assert len(rows) == 1
    assert rows[0]["brand"] == "Bluman"


def test_global_search_finds_article_master_via_brand_alias(tmp_path):
    db_path = str(tmp_path / "am_alias_search.sqlite3")
    _seed_article_master(db_path)
    db = CentralizedDB(db_path)

    results = db.global_search("bluemen", user_id=2)
    rows = results["results"]["article_master"]
    assert len(rows) == 1
    assert rows[0]["brand"] == "Bluman"


def test_global_search_finds_article_master_by_print_style(tmp_path):
    db_path = str(tmp_path / "am_print_search.sqlite3")
    _seed_article_master(db_path)
    db = CentralizedDB(db_path)

    results = db.global_search("digital", user_id=2)
    rows = results["results"]["article_master"]
    assert len(rows) == 1
    assert rows[0]["item_key"] == "BLUMAN|104|DB BS"

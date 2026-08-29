"""Manual Article Master entry (mobile)."""

import sqlite3
from pathlib import Path

import article_master_db as amdb


def _conn(tmp_path):
    db = tmp_path / "am.sqlite3"
    conn = sqlite3.connect(db)
    amdb.ensure_schema(conn)
    amdb.create_category(conn, 1, "Bath", ["brand", "size", "color", "product"], is_confirmed=True)
    return conn


def test_manual_entry_creates_tulip_bathrobe(tmp_path):
    conn = _conn(tmp_path)
    article, created = amdb.create_manual_article(
        conn,
        1,
        {
            "category": "Bath",
            "product_type": "Bathrobe",
            "brand": "Tulip Bathrobe",
            "size": "L",
            "color": "Assorted 12",
            "mrp": 2699,
            "ptr": 1500,
            "ex_mill_price": 1376,
            "bale_pack_size": "12",
            "season_tag": "AW-26",
        },
    )
    assert created is True
    assert article["brand"] == "Tulip Bathrobe"
    assert article["size"] == "Large"
    assert article["product_type"] == "Bathrobe"
    conn.close()


def test_manual_entry_normalizes_tulip_all_bathrobe(tmp_path):
    conn = _conn(tmp_path)
    article, created = amdb.create_manual_article(
        conn,
        1,
        {
            "category": "Bath",
            "product_type": "Bathrobe",
            "brand": "Tulip",
            "size": "ALL",
            "mrp": 2699,
            "ex_mill_price": 1376,
            "season_tag": "AW-26",
        },
    )
    assert created is True
    assert article["brand"] == "Tulip Bathrobe"
    assert article["size"] == "Large"
    conn.close()


def test_manual_entry_upserts_same_key(tmp_path):
    conn = _conn(tmp_path)
    payload = {
        "category": "Bath",
        "product_type": "Terry Towel",
        "brand": "Flora",
        "size": "40x60",
        "color": "White",
        "mrp": 157,
        "ex_mill_price": 85.62,
    }
    _, created1 = amdb.create_manual_article(conn, 1, payload)
    payload["mrp"] = 160
    _, created2 = amdb.create_manual_article(conn, 1, payload)
    assert created1 is True
    assert created2 is False
    count = conn.execute("SELECT COUNT(*) FROM article_master WHERE user_id = 1").fetchone()[0]
    assert count == 1
    conn.close()

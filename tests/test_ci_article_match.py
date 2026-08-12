"""Unit tests for CI ↔ Article Master matching."""

from __future__ import annotations

import sqlite3

import article_master_db as amdb
from ci_article_match import (
    annotate_ci_line_items_with_article_master,
    match_ci_item_to_article,
    rebuild_ci_style_key_from_article,
)


def _mem_conn():
    conn = sqlite3.connect(":memory:")
    amdb.ensure_schema(conn)
    return conn


def test_rebuild_ci_style_key_from_article():
    article = {
        "brand": "Aster",
        "size": "DB BS",
        "item_key": "Aster|100|DB BS",
        "extra_attributes": {"TC": "100"},
    }
    assert rebuild_ci_style_key_from_article(article) == "ASTER|100|DB"


def test_match_ci_item_exact_and_unmatched():
    conn = _mem_conn()
    user_id = 1
    amdb.ensure_default_categories(conn, user_id)
    amdb.ensure_default_brand_aliases(conn, user_id)
    amdb.upsert_article(
        conn,
        user_id,
        {
            "category": "Bed",
            "brand": "Aster",
            "size": "DB BS",
            "product_type": "Bedsheet",
            "mrp": 1000,
            "ptr": 800,
            "ex_mill_price": 500,
            "bale_pack_size": 36,
            "item_key": "Aster|100|DB BS",
            "extra_attributes": {"TC": "100"},
            "is_active": 1,
            "source_filename": "test.xlsx",
            "workspace_id": "default",
        },
        source_filename="test.xlsx",
        workspace_id="default",
    )

    hit = match_ci_item_to_article(
        conn, amdb, user_id, item_key="ASTER|100|DB", item_name="ASTER 1+2 DB SET 100TC"
    )
    assert hit["status"] == "matched"
    assert hit["article_id"] is not None

    miss = match_ci_item_to_article(
        conn, amdb, user_id, item_key="UNKNOWN|100|DB", item_name="UNKNOWN SET 100TC"
    )
    assert miss["status"] == "unmatched"

    lines, summary = annotate_ci_line_items_with_article_master(
        conn,
        amdb,
        user_id,
        [
            {"item_name": "ASTER 1+2 DB SET 100TC", "item_key": "ASTER|100|DB", "qty": 2, "value": 100},
            {"item_name": "NO KEY LINE", "item_key": None, "qty": 1, "value": 50},
        ],
    )
    assert summary["matched"] == 1
    assert summary["no_key"] == 1
    assert lines[0]["article_match"]["status"] == "matched"

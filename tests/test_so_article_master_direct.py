"""SO-direct (no Filled Order) save via Article Master matching."""

from __future__ import annotations

import sqlite3

import article_master_db as amdb
from app.services import fo_so_match_db as matchdb
from app.services.so_article_master_match import (
    MATCH_MODE_AM_ONLY,
    preview_so_article_master_match,
    save_so_article_master_only,
)


def _conn(tmp_path):
    path = tmp_path / "so_am.sqlite3"
    conn = sqlite3.connect(str(path))
    matchdb.ensure_schema(conn)
    amdb.ensure_schema(conn)
    return conn


def _seed_aster(conn, user_id: int = 1) -> None:
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


def _so_pack() -> dict:
    return {
        "line_detail": [
            {
                "so_number": "102876999",
                "material_code": "ASTER DB",
                "product_detail": "ASTER 1+2 DB SET 224X244 7985BLU 100TC",
                "qty": 120,
                "net_amount": 60000,
                "gst_amount": 0,
                "total_amount": 60000,
            },
            {
                "so_number": "102876999",
                "material_code": "UNKNOWN",
                "product_detail": "UNKNOWN BRAND 1+2 DB SET 100TC",
                "qty": 10,
                "net_amount": 5000,
                "gst_amount": 0,
                "total_amount": 5000,
            },
        ],
        "meta": {
            "source_filename": "direct_so.zip",
            "primary_buyer_name": "Test Distributor",
            "dominant_category": "Bed",
        },
    }


def test_preview_so_article_master_match(tmp_path):
    conn = _conn(tmp_path)
    _seed_aster(conn)
    data = preview_so_article_master_match(conn, 1, _so_pack())
    summary = data["article_master_match"]
    assert summary["matched"] >= 1
    assert summary["total"] == 2


def test_save_so_article_master_only_no_fo(tmp_path):
    conn = _conn(tmp_path)
    uid = 1
    _seed_aster(conn)
    pack = _so_pack()
    run = save_so_article_master_only(
        conn,
        uid,
        so_pack=pack,
        so_buyer_label="Test Distributor",
        so_source_filename="direct_so.zip",
        category="Bed",
        season="AW26",
    )
    assert run.get("filled_order_id") is None
    assert run.get("match_mode") == MATCH_MODE_AM_ONLY
    assert (run.get("match_count") or 0) >= 1
    assert run.get("fo_qty") is None

    listed = matchdb.list_match_runs(conn, user_id=uid)
    assert len(listed) == 1
    assert listed[0].get("match_mode") == MATCH_MODE_AM_ONLY
    assert (listed[0].get("match_count") or 0) >= 1


def test_duplicate_so_blocked_for_am_only(tmp_path):
    conn = _conn(tmp_path)
    uid = 2
    _seed_aster(conn)
    pack = _so_pack()
    save_so_article_master_only(conn, uid, so_pack=pack, so_buyer_label="Test")
    try:
        save_so_article_master_only(conn, uid, so_pack=pack, so_buyer_label="Test")
        assert False, "expected DuplicateSalesOrderError"
    except matchdb.DuplicateSalesOrderError:
        pass

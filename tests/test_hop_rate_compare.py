"""Smoke tests for HoP multi-supplier rate comparison."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.hop_schema import HOP_WORKSPACE_ID, ensure_hop_schema
from app import hop_ops
from app.hop_rate_compare import (
    build_comparison_matrix,
    lines_from_structured,
    product_match_key,
    SAMPLE_SHEETS,
)


def test_product_key_near_matches():
    assert product_match_key("Hand towel", "16x24") == product_match_key("Hand Towel", "16x25")
    assert product_match_key("Bath Mat", "20x30") == product_match_key("Bath Mat", "21x30")
    assert "bedsheet" in product_match_key("King Bedsheet", "110x112", "300 TC")
    # Same size bedsheet must match across vendors even if one quotes TC in the name
    assert product_match_key("Bed Sheet", "110x112") == product_match_key(
        "300TC Plain Bedsheet", "110x112", "300 TC plain"
    )
    # Spa vs Premium must stay separate rows
    assert product_match_key("Spa Face Towel", "12x12") != product_match_key(
        "Hotel Plain Premium Face Towel", "12x12"
    )
    # Welspun mill CM sizes + Large Towel (= bathsheet) align with inch hotel quotes
    assert product_match_key("Large Towel", "91CMX183CM", "550 GSM") == "bath_sheet|36x72|550gsm"
    assert product_match_key("Large Towel", "91CMX183CM", "550 GSM") == product_match_key(
        "Bathsheet", "36x72", "550 GSM"
    )
    assert product_match_key("Bath Towel", "76CMX152CM", "550 GSM") == product_match_key(
        "Bath Towel", "30x60", "550 GSM"
    )
    assert product_match_key("Large Towel", "91CMX183CM", "550 GSM") != product_match_key(
        "Large Towel", "91CMX183CM", "630 GSM"
    )


def test_matrix_merges_same_size_bedsheet_across_vendors():
    sheets = [
        {
            "id": 1,
            "supplier_name": "UMD",
            "lines": [
                {
                    "product_key": "bedsheet|110x112|300tc",  # legacy key still merges
                    "product_name": "300TC Plain Bedsheet",
                    "display_name": "Bedsheet 110x112",
                    "size": "110x112",
                    "rate": 760,
                    "gst_pct": 5,
                    "landed_rate": 798,
                }
            ],
        },
        {
            "id": 2,
            "supplier_name": "Bharat",
            "lines": [
                {
                    "product_key": "bedsheet|110x112",
                    "product_name": "Bed Sheet",
                    "display_name": "Bedsheet 110x112",
                    "size": "110x112",
                    "rate": 715,
                    "gst_pct": 5,
                    "landed_rate": 750.75,
                }
            ],
        },
    ]
    matrix = build_comparison_matrix(sheets)
    beds = [p for p in matrix["products"] if "bedsheet" in (p.get("product_key") or "")]
    assert len(beds) == 1
    offers = beds[0]["offers"]
    assert offers["1"]["rate"] == 760
    assert offers["2"]["rate"] == 715
    assert beds[0]["best"]["supplier_name"] == "Bharat"
    assert beds[0]["supplier_count"] == 2


def test_matrix_skips_empty_sheets():
    sheets = [
        {"id": 1, "supplier_name": "UMD", "lines": []},
        {
            "id": 2,
            "supplier_name": "Bharat",
            "lines": [
                {
                    "product_name": "Bath Mat",
                    "size": "20x30",
                    "rate": 199,
                    "gst_pct": 5,
                    "landed_rate": 208.95,
                }
            ],
        },
        {"id": 3, "supplier_name": "Bharat", "lines": []},
    ]
    matrix = build_comparison_matrix(sheets)
    assert len(matrix["suppliers"]) == 1
    assert matrix["suppliers"][0]["supplier_name"] == "Bharat"
    assert matrix["summary"]["supplier_count"] == 1
    assert len(matrix["products"]) == 1
    assert "1" not in matrix["products"][0]["offers"]
    assert "3" not in matrix["products"][0]["offers"]


def test_clear_product_matches_live_key_when_stored_key_stale(tmp_path: Path):
    db = tmp_path / "hop_clear_keys.sqlite3"
    ensure_hop_schema(str(db))
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        sheet = hop_ops.create_rate_sheet(
            conn,
            HOP_WORKSPACE_ID,
            {
                "supplier_name": "GSB",
                "source_type": "pdf",
                "lines": [
                    {"product_name": "Spa Face Towel", "size": "12x12", "rate": 39, "gst_pct": 5},
                    {"product_name": "Spa Hand Towel", "size": "16x24", "rate": 90, "gst_pct": 5},
                ],
            },
        )
        # Simulate old stored key (pre-variant)
        conn.execute(
            "UPDATE hop_rate_lines SET product_key='face_towel' WHERE workspace_id=? AND product_name LIKE 'Spa Face%'",
            (HOP_WORKSPACE_ID,),
        )
        conn.commit()
        live = product_match_key("Spa Face Towel", "12x12")
        assert "spa" in live
        result = hop_ops.clear_rate_lines(conn, HOP_WORKSPACE_ID, product_keys=[live])
        assert result["deleted_lines"] == 1
        left = conn.execute(
            "SELECT product_name FROM hop_rate_lines WHERE workspace_id=?",
            (HOP_WORKSPACE_ID,),
        ).fetchall()
        assert len(left) == 1
        assert left[0]["product_name"] == "Spa Hand Towel"
        # clear all
        hop_ops.clear_rate_lines(conn, HOP_WORKSPACE_ID, clear_all=True)
        assert conn.execute("SELECT COUNT(*) c FROM hop_rate_lines").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM hop_rate_sheets").fetchone()["c"] == 0
    finally:
        conn.close()


def test_matrix_fills_missing_as_zero_and_keeps_all_products(tmp_path: Path):
    db = tmp_path / "hop_rates_full.sqlite3"
    ensure_hop_schema(str(db))
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        hop_ops.seed_sample_rate_sheets(conn, HOP_WORKSPACE_ID, replace=True)
        matrix = hop_ops.rate_comparison_matrix(conn, HOP_WORKSPACE_ID)
        assert matrix["summary"]["product_count"] >= 20
        assert matrix["summary"]["single_quote_count"] >= 1
        # Every product has a column for every supplier
        supplier_ids = {str(s["sheet_id"]) for s in matrix["suppliers"]}
        for p in matrix["products"]:
            assert set(p["offers"].keys()) == supplier_ids
            for offer in p["offers"].values():
                assert "rate" in offer
                if offer.get("missing"):
                    assert offer["rate"] == 0
        # Suggestions only for multi-quote products
        for s in matrix["suggestions"]:
            assert s["alternatives"] >= 2
    finally:
        conn.close()


def test_sample_sheets_normalize():
    lines = lines_from_structured(SAMPLE_SHEETS["ambala"]["lines"])
    assert len(lines) == 8
    assert all(l["landed_rate"] and l["landed_rate"] > l["rate"] for l in lines)

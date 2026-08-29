"""Tests for order stream classification (regular vs special)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.order_stream import (
    STREAM_MIXED,
    STREAM_REGULAR,
    STREAM_SPECIAL,
    annotate_so_pack_meta,
    classify_fo_stream,
    classify_so_header_stream,
    classify_so_pack_stream,
    filter_so_pack_by_stream,
    normalize_po_family,
)


def test_normalize_po_family_strips_spl_and_towel():
    assert normalize_po_family("RFA 0381 TOWEL SPL") == "RFA 0381"
    assert normalize_po_family("RFA 0381 TOWEL") == "RFA 0381"


def test_classify_so_header_from_po():
    assert classify_so_header_stream(po_number="RFA 0381 TOWEL SPL") == STREAM_SPECIAL
    assert classify_so_header_stream(po_number="RFA 0381 TOWEL") == STREAM_REGULAR
    assert classify_so_header_stream(source_pdf="BND SPL 102876664.pdf") == STREAM_SPECIAL


def test_classify_fo_stream_from_filename():
    assert classify_fo_stream("BND Bath linen special order.xlsx") == STREAM_SPECIAL
    assert classify_fo_stream("BND regular towel orders.xlsx") == STREAM_REGULAR


def test_streams_compatible():
    from app.services.order_stream import streams_compatible

    assert streams_compatible("regular", "regular") is True
    assert streams_compatible("special", "special") is True
    assert streams_compatible("regular", "special") is False
    assert streams_compatible("special", "regular") is False
    assert streams_compatible("special", "mixed") is True  # caller filters before match


def test_build_mixed_zip_retry_hint_points_to_sibling_fo():
    import sqlite3

    import filled_orders_db as fodb
    from app.services import fo_so_match_db as matchdb
    from app.services.order_stream import build_mixed_zip_retry_hint

    conn = sqlite3.connect(":memory:")
    fodb.ensure_schema(conn)
    matchdb.ensure_schema(conn)
    special_id = fodb.create_filled_order(
        conn, 1, 10, "BND", "Bath", "AW-26",
        source_filename="special.xlsx", order_stream="special",
    )
    regular_id = fodb.create_filled_order(
        conn, 1, 10, "BND", "Bath", "AW-26",
        source_filename="regular.xlsx", order_stream="regular",
    )
    conn.execute(
        """INSERT INTO fo_so_match_runs (
            id, user_id, filled_order_id, so_source_filename, rows_json, created_at
        ) VALUES (1, 1, ?, 'bnd.zip', '[]', '2026-01-01')""",
        (special_id,),
    )
    conn.commit()
    fo = fodb.get_filled_order(conn, 1, special_id)
    hint = build_mixed_zip_retry_hint(
        conn,
        user_id=1,
        fo=fo,
        so_source_filename="bnd.zip",
        pack_was_mixed=True,
    )
    assert hint is not None
    assert hint["hint_code"] == "match_other_stream_fo"
    assert hint["other_filled_order_id"] == regular_id
    assert "Regular" in hint["message"]


def test_mixed_so_pack_filters_to_regular():
    pack = {
        "meta": {"source_filename": "bnd.zip"},
        "so_summary": [
            {"so_number": "102876610", "po_number": "RFA 0381 TOWEL", "source_pdf": "BND 102876610.pdf"},
            {"so_number": "102876664", "po_number": "RFA 0381 TOWEL SPL", "source_pdf": "BND SPL 102876664.pdf"},
        ],
        "line_detail": [
            {"so_number": "102876610", "po_number": "RFA 0381 TOWEL", "qty": 100, "product_name": "FLORA", "net_amount": 1000},
            {"so_number": "102876664", "po_number": "RFA 0381 TOWEL SPL", "qty": 7077, "product_name": "SANTINO", "net_amount": 2000},
        ],
        "consolidated": [],
    }
    assert classify_so_pack_stream(pack) == STREAM_MIXED
    regular = filter_so_pack_by_stream(pack, STREAM_REGULAR)
    assert len(regular["line_detail"]) == 1
    assert regular["line_detail"][0]["so_number"] == "102876610"
    special = filter_so_pack_by_stream(pack, STREAM_SPECIAL)
    assert len(special["line_detail"]) == 1
    assert special["line_detail"][0]["so_number"] == "102876664"


def test_annotate_so_pack_meta_mixed():
    pack = annotate_so_pack_meta({
        "meta": {},
        "so_summary": [
            {"so_number": "1", "po_number": "PO TOWEL"},
            {"so_number": "2", "po_number": "PO TOWEL SPL"},
        ],
        "line_detail": [],
        "consolidated": [],
    })
    assert pack["meta"]["order_stream"] == STREAM_MIXED
    assert pack["meta"]["mixed_streams"] is True


def test_infer_so_pack_dominant_category_towel_lines():
    from app.services.order_stream import infer_so_pack_dominant_category

    pack = {
        "line_detail": [
            {
                "product_name": "TULIP DYED 40CM X 60CM",
                "product_detail": "HAND TOWEL",
                "qty": 100,
            },
            {
                "product_name": "TULIP R4 SET",
                "qty": 50,
            },
        ],
        "so_summary": [
            {
                "so_number": "1",
                "buyer_name": "BERNINA INTERNATIONAL P LTD",
                "source_pdf": "bnd_towel_regular.pdf",
            }
        ],
    }
    assert infer_so_pack_dominant_category(pack) == "Bath"
    annotated = annotate_so_pack_meta({**pack, "meta": {}, "consolidated": []})
    assert annotated["meta"]["dominant_category"] == "Bath"
    assert annotated["meta"]["primary_buyer_name"] == "BERNINA INTERNATIONAL P LTD"


@pytest.mark.skipif(
    not Path(r"G:\My Drive\2026-2027\Oder Management\AW26 order\Towel\SO\bnd.zip").exists(),
    reason="BND AW26 towel fixtures on G: drive not available",
)
def test_bnd_aw26_zip_classifies_mixed_streams():
    import zipfile
    from app.services.so_pack_consolidate import analyze_so_pack

    zip_path = Path(r"G:\My Drive\2026-2027\Oder Management\AW26 order\Towel\SO\bnd.zip")
    payload = analyze_so_pack(zip_path.read_bytes(), zip_path.name)
    meta = payload.get("meta") or {}
    assert meta.get("order_stream") == STREAM_MIXED
    counts = meta.get("stream_so_counts") or {}
    assert counts.get(STREAM_REGULAR) == 4
    assert counts.get(STREAM_SPECIAL) == 3
    regular = filter_so_pack_by_stream(payload, STREAM_REGULAR)
    special = filter_so_pack_by_stream(payload, STREAM_SPECIAL)
    assert len(regular.get("so_summary") or []) == 4
    assert len(special.get("so_summary") or []) == 3
    assert normalize_po_family((regular["so_summary"][0] or {}).get("po_number")) == "RFA 0381"

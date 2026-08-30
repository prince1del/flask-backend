"""Drive folder path helpers for Order Desk backups."""

from app.storage.nexora_drive_paths import (
    build_order_desk_drive_segments,
    normalize_category_label,
    normalize_season_label,
    season_from_order_sheet_name,
    so_pdf_drive_contexts,
)


def test_build_order_desk_drive_segments():
    segments = build_order_desk_drive_segments(
        season="AW26 Bedsheet",
        category="bath",
        distributor_name="Balaji Homedecor",
    )
    assert segments == ["AW26", "Bath", "Balaji Homedecor"]


def test_season_from_order_sheet_name():
    assert season_from_order_sheet_name("Order Sheets SS26") == "SS26"
    assert season_from_order_sheet_name("AW26 Bedsheet Master") == "AW26"


def test_so_pdf_drive_contexts_per_buyer():
    analyze = {
        "meta": {"dominant_category": "Bed", "primary_buyer_name": "Bernina International P Ltd"},
        "so_summary": [
            {
                "source_pdf": "BND 102876560.pdf",
                "buyer_name": "Bernina International P Ltd",
                "order_date": "2026-08-15",
            },
            {
                "source_pdf": "BLJ 102876310.pdf",
                "buyer_name": "Balaji Homedecor",
                "order_date": "2026-08-20",
            },
        ],
        "line_detail": [
            {"source_pdf": "BND 102876560.pdf", "product_name": "525B BEDSHEET", "qty": 10},
            {"source_pdf": "BLJ 102876310.pdf", "product_name": "TOWEL 70CM", "qty": 5},
        ],
    }
    ctx = so_pdf_drive_contexts(analyze, workspace_id="ws")
    assert ctx["BND 102876560.pdf"]["distributor_name"] == "Bernina International P Ltd"
    assert ctx["BND 102876560.pdf"]["category"] == "Bed"
    assert ctx["BND 102876560.pdf"]["season"] == "AW26"
    assert ctx["BLJ 102876310.pdf"]["distributor_name"] == "Balaji Homedecor"
    assert ctx["BLJ 102876310.pdf"]["category"] == "Bath"


def test_normalize_category_label():
    assert normalize_category_label("bedsheet") == "Bed"
    assert normalize_category_label("") == "Others"

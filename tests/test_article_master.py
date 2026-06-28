from pathlib import Path

import pandas as pd

from centralized_db_system.article_master import ArticleMasterService
from centralized_db_system.db import CentralizedDB


def test_article_sanitization_and_save(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "article_master.sqlite3"))
    service = ArticleMasterService(str(db.db_path))

    article_id = service.save_article(
        {
            "category_name": "towels",
            "design_code": "A100",
            "color_way": "blue",
            "base_rate": 1200,
            "gst_percentage": 5,
            "pcs_per_bale": 40,
        }
    )

    articles = service.list_articles_by_category()
    assert article_id > 0
    assert articles[0]["category_name"] == "Towels"
    assert articles[0]["design_name"] == "A100"
    assert articles[0]["color_way"] == "BLUE"
    assert articles[0]["base_rate"] == 1200


def test_article_service_merges_messy_categories(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "article_master_merge.sqlite3"))
    service = ArticleMasterService(str(db.db_path))

    service.save_article(
        {
            "category_name": "Bombay Dyeing Towel",
            "design_code": "B200",
            "color_way": "red",
            "base_rate": 900,
            "gst_percentage": 12,
            "pcs_per_bale": 20,
        }
    )
    sanitized = service.sanitize_article_payload(
        {
            "category_name": "towels",
            "design_code": "B201",
            "color_way": "green",
            "base_rate": 950,
            "gst_percentage": 12,
            "pcs_per_bale": 20,
        },
        existing_categories=["Towels"],
    )

    assert sanitized["category_name"] == "Towels"


def test_build_article_master_from_order_sheet(tmp_path: Path) -> None:
    db = CentralizedDB(str(tmp_path / "article_master_from_order.sqlite3"))
    order_sheet = tmp_path / "order_sheet.xlsx"

    df = pd.DataFrame(
        [
            {
                "Brand": "Aster",
                "Size": "DB BS",
                "Product": "Sheet Sets",
                "Print Style": "Pigment",
                "ExMill Price": 625,
                "Min bale pack": 216,
            },
            {
                "Brand": "Aster",
                "Size": "DB BS",
                "Product": "Sheet Sets",
                "Print Style": "Pigment",
                "ExMill Price": 625,
                "Min bale pack": 216,
            },
            {
                "Brand": "Cardinal",
                "Size": "SB BS",
                "Product": "Sheet Sets",
                "Print Style": "Pigment",
                "ExMill Price": 536,
                "Min bale pack": 144,
            },
        ]
    )
    df.to_excel(order_sheet, index=False)

    result = db.build_article_master_from_order_sheet(order_sheet)
    rows = db.list_articles_by_category()

    assert result["inserted"] == 2
    assert result["duplicates"] == 1
    assert result["rows_processed"] == 3
    assert len(rows) == 2
    assert any(item["design_name"].lower() == "aster db bs" for item in rows)
    assert any(item["design_name"].lower() == "cardinal sb bs" for item in rows)

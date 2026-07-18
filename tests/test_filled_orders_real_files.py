"""
Optional integration tests against real distributor files on Google Drive.
Skipped automatically when files are not present on this machine.
"""

from pathlib import Path

import pytest

REAL_FILES = [
    ("Bed", Path(r"G:\My Drive\2026-2027\AW26 order sh\Bedsheet\Choice Corner.xlsx")),
    ("Bed", Path(r"G:\My Drive\2026-2027\AW26 order sh\Bedsheet\DCA Order.xlsx")),
    ("Bed", Path(r"G:\My Drive\2026-2027\AW26 order sh\Bedsheet\kag.xlsx")),
    ("Bed", Path(r"G:\My Drive\2026-2027\AW26 order sh\Bedsheet\BND.xlsx")),
    ("Bath", Path(r"G:\My Drive\2026-2027\AW26 order sh\Towel\KAG AGRA.xlsx")),
    ("Bath", Path(r"G:\My Drive\2026-2027\AW26 order sh\Towel\ptj.xlsx")),
    ("Bath", Path(r"G:\My Drive\2026-2027\AW26 order sh\Towel\savitri steel.xlsx")),
    ("Bath", Path(r"G:\My Drive\2026-2027\AW26 order sh\Towel\BND.xlsx")),
]

LIVE_DB = Path(__file__).resolve().parent.parent / "centralized_db.sqlite3"
USER_ID = 2

pytestmark = pytest.mark.skipif(
    not all(p.exists() for _, p in REAL_FILES),
    reason="Real distributor Excel files not available on G: drive",
)


def test_real_files_qty_detection_and_match_rate():
    import sqlite3

    import article_master_db as amdb
    import article_master_parser as amparser
    import filled_orders_parser as foparser
    import pandas as pd

    conn = sqlite3.connect(LIVE_DB)
    categories = amdb.get_all_categories(conn, USER_ID)
    key_fields_lookup = {c["category_name"]: c["key_fields"] for c in categories}

    total_ordered = total_matched = 0

    for expected_category, path in REAL_FILES:
        raw_df = pd.read_excel(path, sheet_name=0, header=None)
        header_idx = amparser.detect_header_row(raw_df)
        header_row = raw_df.iloc[header_idx].tolist()
        col_mapping = amparser.map_columns_to_core(header_row)
        valid_rows = [
            row for _, row in raw_df.iloc[header_idx + 1:].iterrows()
            if amparser.is_data_row(row.tolist(), col_mapping)
        ]

        qty_det = foparser.detect_quantity_column(
            header_row, col_mapping, expected_category, valid_rows,
        )
        assert qty_det["status"] == "ok", f"{path.name}: qty detection failed — {qty_det}"

        if path.name == "BND.xlsx" and expected_category == "Bed":
            assert qty_det["column_label"] == "Additional Order Qty"

        parsed = foparser.build_filled_order_rows(
            valid_rows, header_row, col_mapping, qty_det["column_index"],
        )
        key_fields = key_fields_lookup.get(expected_category, ["brand", "size"])
        matched = sum(
            1 for row in parsed
            if foparser.match_and_normalize(
                conn, amdb, USER_ID, row, key_fields, category=expected_category,
            )["matched"]
        )
        total_ordered += len(parsed)
        total_matched += matched
        assert matched == len(parsed), f"{path.name}: {matched}/{len(parsed)} matched"

    conn.close()
    assert total_matched == total_ordered
    assert total_ordered >= 200

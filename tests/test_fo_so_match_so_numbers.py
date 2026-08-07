"""Order Match rows should carry SO numbers from SO Pack line detail."""

from app.services.fo_so_match_lab import (
    build_so_buckets_from_line_detail,
    compare_fo_so_buckets,
)


def test_compare_rows_include_so_numbers_from_line_detail():
    so = build_so_buckets_from_line_detail(
        [
            {
                "so_number": "102876117",
                "product_name": "ASTER 1+2 DB SET",
                "qty": 100,
                "net_amount": 50000,
            },
            {
                "so_number": "102876200",
                "product_name": "ASTER 1+2 DB SET",
                "qty": 44,
                "net_amount": 22000,
            },
        ]
    )
    fo = {
        next(iter(so["buckets"])): {
            "brand": "Aster",
            "size": "DB BS",
            "qty": 144,
            "value": 72000,
            "so_numbers": [],
        }
    }
    result = compare_fo_so_buckets(fo, so["buckets"])
    assert result["rows"]
    row = result["rows"][0]
    assert row["status"] in {"MATCH", "MATCH_FUZZY_BRAND", "VALUE_MISMATCH", "QTY_MISMATCH"}
    assert row["so_numbers"] == ["102876117", "102876200"]

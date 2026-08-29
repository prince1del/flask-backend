"""Towel SO PDF product_name → brand × size for FO match."""

from app.services.bd_product_catalog import resolve_so_brand_size
from app.services.fo_so_match_lab import build_so_buckets_from_line_detail, compare_fo_so_buckets


def test_tulip_hand_towel_from_so_pdf_name():
    brand, size = resolve_so_brand_size("TULIP DYED 40CM X 60CM ASST12 AW26")
    assert brand == "Tulip"
    assert size == "Hand Towel"


def test_flora_bathrobe_from_so_pdf_name():
    brand, size = resolve_so_brand_size("FLORA BATHROBE DYED ASST. SIZE-L AW26")
    assert brand == "Flora Bathrobe"
    assert size == "Large"


def test_flora_bath_towel_from_so_pdf_name():
    brand, size = resolve_so_brand_size("FLORA DYED 75CM X 1.5M ASST12 AW26")
    assert brand == "Flora"
    assert size == "Bath Towel"


def test_so_bucket_builds_towel_lines():
    lines = [
        {
            "product_name": "TULIP DYED 40CM X 60CM ASST12 AW26",
            "qty": 100.0,
            "net_amount": 10000.0,
            "gst_amount": 1800.0,
            "total_amount": 11800.0,
        },
        {
            "product_name": "FLORA BATHROBE DYED ASST. SIZE-L AW26",
            "qty": 50.0,
            "net_amount": 50000.0,
            "gst_amount": 9000.0,
            "total_amount": 59000.0,
        },
    ]
    so = build_so_buckets_from_line_detail(lines)
    assert so["line_count"] == 2
    assert so["others_qty"] == 0.0
    keys = set(so["buckets"].keys())
    assert ("tulip", "hand towel") in keys
    assert ("flora bathrobe", "large") in keys


def test_fo_flora_bath_towel_matches_so_pdf_bucket():
    fo = {
        ("flora", "bath towel"): {
            "brand": "FLORA",
            "size": "Bath Towel",
            "qty": 2250.0,
            "value": 1012500.0,
        }
    }
    so = build_so_buckets_from_line_detail(
        [
            {
                "product_name": "FLORA DYED 75CM X 1.5M ASST12 AW26",
                "qty": 2250.0,
                "net_amount": 1012500.0,
            }
        ]
    )
    result = compare_fo_so_buckets(fo, so["buckets"])
    assert result["counts"]["MISSING_ON_SO"] == 0
    assert result["counts"]["MATCH"] + result["counts"]["VALUE_MISMATCH"] == 1

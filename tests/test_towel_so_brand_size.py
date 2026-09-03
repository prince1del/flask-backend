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


def test_flora_bathrobe_xl_from_so_pdf_name():
    brand, size = resolve_so_brand_size("FLORA BATHROBE DYED ASST. SIZE-XL AW26")
    assert brand == "Flora Bathrobe"
    assert size == "Extra Large"


def test_flora_bathrobe_l_and_xl_match_separately():
    from app.services.fo_so_match_lab import _bucket_add

    fo: dict = {}
    so: dict = {}
    _bucket_add(fo, brand="Flora Bathrobe", size="Large", qty=120, value=160000)
    _bucket_add(fo, brand="Flora Bathrobe", size="Extra Large", qty=120, value=131000)
    lines = [
        {
            "product_name": "FLORA BATHROBE DYED ASST. SIZE-L AW26",
            "qty": 120.0,
            "net_amount": 160000.0,
            "gst_amount": 0.0,
            "total_amount": 160000.0,
            "so_number": "102876540",
        },
        {
            "product_name": "FLORA BATHROBE DYED ASST. SIZE-XL AW26",
            "qty": 120.0,
            "net_amount": 131000.0,
            "gst_amount": 0.0,
            "total_amount": 131000.0,
            "so_number": "102876540",
        },
    ]
    built = build_so_buckets_from_line_detail(lines)
    for key, row in built["buckets"].items():
        _bucket_add(
            so,
            brand=row["brand"],
            size=row["size"],
            qty=row["qty"],
            value=row["value"],
            so_number="102876540",
        )
    result = compare_fo_so_buckets(fo, so)
    assert result["counts"]["MISSING_ON_SO"] == 0
    assert result["counts"]["EXTRA_ON_SO"] == 0
    assert result["counts"]["QTY_MISMATCH"] == 0
    assert result["counts"]["MATCH"] + result["counts"]["MATCH_FUZZY_BRAND"] == 2


def test_luxury_living_uses_product_detail_when_name_truncated():
    lines = [
        {
            "product_name": "LUXURY LIVING DYED",
            "product_detail": "LUXURY LIVING DYED 75CM X 1.5M ASST04 AW26",
            "qty": 144.0,
            "net_amount": 174000.0,
            "gst_amount": 0.0,
            "total_amount": 174000.0,
            "so_number": "1",
        },
        {
            "product_name": "LUXURY LIVING DYED",
            "product_detail": "LUXURY LIVING DYED 40CM X 60CM ASST04 AW26",
            "qty": 240.0,
            "net_amount": 92000.0,
            "gst_amount": 0.0,
            "total_amount": 92000.0,
            "so_number": "1",
        },
    ]
    so = build_so_buckets_from_line_detail(lines)
    keys = set(so["buckets"].keys())
    assert ("luxury living", "bath towel") in keys
    assert ("luxury living", "hand towel") in keys
    assert so["others_qty"] == 0.0


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


def test_flip_towel_is_one_side_terry():
    brand, size = resolve_so_brand_size("FLIP TOWEL DYED 75CM X 1.5M ASST04 AW26")
    assert brand == "One side Terry"
    assert size == "Bath Towel"
    brand_h, size_h = resolve_so_brand_size("FLIP TOWEL DYED 40CM X 60CM ASST04 AW26")
    assert brand_h == "One side Terry"
    assert size_h == "Hand Towel"


def test_fo_one_side_terry_matches_so_flip_towel():
    from app.services.fo_so_match_lab import _bucket_add

    fo: dict = {}
    so: dict = {}
    _bucket_add(fo, brand="One side Terry", size="Bath Towel", qty=240, value=90134)
    _bucket_add(fo, brand="One side Terry", size="Hand Towel", qty=288, value=24346)
    lines = [
        {
            "product_name": "FLIP TOWEL DYED 75CM X 1.5M ASST04 AW26",
            "qty": 240.0,
            "net_amount": 90134.0,
            "so_number": "102876584",
        },
        {
            "product_name": "FLIP TOWEL DYED 40CM X 60CM ASST04 AW26",
            "qty": 288.0,
            "net_amount": 24346.0,
            "so_number": "102876584",
        },
    ]
    built = build_so_buckets_from_line_detail(lines)
    for key, row in built["buckets"].items():
        _bucket_add(
            so,
            brand=row["brand"],
            size=row["size"],
            qty=row["qty"],
            value=row["value"],
            so_number="102876584",
        )
    result = compare_fo_so_buckets(fo, so)
    assert result["counts"]["MISSING_ON_SO"] == 0
    assert result["counts"]["EXTRA_ON_SO"] == 0
    assert result["counts"]["MATCH"] + result["counts"]["MATCH_FUZZY_BRAND"] >= 2

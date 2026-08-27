"""FO ↔ SO brand teaching: Florentine / Allure ≡ Allure."""

from app.services.fo_so_match_lab import (
    brands_equivalent,
    compare_fo_so_buckets,
    soft_brand_key,
    soft_size_key,
)


def test_florentine_allure_same_soft_key():
    assert soft_brand_key("Florentine / Allure") == soft_brand_key("Allure")
    assert soft_brand_key("Florentine / Allure") == "allure"
    assert brands_equivalent("Florentine / Allure", "Allure")


def test_kb_fs_equals_king_fitted_sheet():
    assert soft_size_key("KB FS") == soft_size_key("King Fitted Sheet")


def test_florentine_allure_compare_matches():
    fo = {
        ("allure", "kb fs"): {
            "brand": "Florentine / Allure",
            "size": "KB FS",
            "qty": 144.0,
            "value": 166123.0,
        }
    }
    so = {
        ("allure", "kb fs"): {
            "brand": "Allure",
            "size": "King Fitted Sheet",
            "qty": 144.0,
            "value": 166122.72,
        }
    }
    # Build with raw keys as soft_brand_key would produce
    result = compare_fo_so_buckets(fo, so)
    assert result["counts"]["MISSING_ON_SO"] == 0
    assert result["counts"]["EXTRA_ON_SO"] == 0
    assert result["counts"]["MATCH"] + result["counts"]["MATCH_FUZZY_BRAND"] == 1
    row = result["rows"][0]
    assert row["fo_qty"] == 144
    assert row["so_qty"] == 144


def test_display_size_always_short_code():
    from app.services.fo_so_match_lab import display_size_code, _bucket_add, compare_fo_so_buckets

    assert display_size_code("King Fitted Sheet") == "KB FS"
    assert display_size_code("KB FS") == "KB FS"

    fo: dict = {}
    so: dict = {}
    _bucket_add(fo, brand="Florentine / Allure", size="KB FS", qty=144, value=166123)
    _bucket_add(so, brand="Allure", size="King Fitted Sheet", qty=144, value=166122.72)
    assert list(fo.keys()) == list(so.keys()) == [("allure", "kb fs")]
    assert fo[("allure", "kb fs")]["size"] == "KB FS"
    assert so[("allure", "kb fs")]["size"] == "KB FS"
    result = compare_fo_so_buckets(fo, so)
    assert result["counts"]["MISSING_ON_SO"] == 0
    assert result["counts"]["EXTRA_ON_SO"] == 0
    assert result["rows"][0]["size"] == "KB FS"
    assert result["rows"][0]["status"] in ("MATCH", "MATCH_FUZZY_BRAND")


def test_value_tol_plus_minus_10_is_match():
    fo = {
        ("blumen", "db bs"): {
            "brand": "Blumen", "size": "DB BS", "qty": 108.0, "value": 100000.0,
        }
    }
    so = {
        ("blumen", "db bs"): {
            "brand": "Blumen", "size": "DB BS", "qty": 108.0, "value": 100007.99,
        }
    }
    result = compare_fo_so_buckets(fo, so)
    assert result["counts"]["MATCH"] == 1
    assert result["counts"]["VALUE_MISMATCH"] == 0

    # Large lines allow ±0.5% (FO bale→pcs ExMill rounding); beyond that the
    # line is a real value mismatch.
    so[("blumen", "db bs")]["value"] = 100000.0 * 1.006
    result2 = compare_fo_so_buckets(fo, so)
    assert result2["counts"]["VALUE_MISMATCH"] == 1

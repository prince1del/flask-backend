"""Bath/towel FO ↔ SO teaching maps."""

from __future__ import annotations

from pathlib import Path

from app.services.bd_product_catalog import enrich_bd_product
from app.services.fo_so_match_lab import run_match_lab_files


def test_towel_enrich_examples():
    cases = {
        "NATURE'S BQT DYD 2PC 40CMX60CM AST06AW26": ("Bamboo", "Hand Towel Set of 2"),
        "SANTINO PRE 75CMX1.5M ASST12 AW26": ("Santino", "Bath Towel"),
        "SANTINO PRE DYED 2PC 40X60CM ASST12 AW26": ("Santino", "Hand Towel Set of 2"),
        "BD WHITE 40CM X 60CM-WHITE AW26": ("BD White", "Hand Towel"),
        "GYM TOWEL DYED 50CMX100CM ASST04 AW26": ("Gym Towel", "Gym Towel"),
        "COOLTEX 72CMCX1.44M ASST AW26": ("Rimzim Cooltex", "Bath Towel"),
        "HUCK A BUCK 72CM X 1.44M ASST AW26": ("Huk A Buk", "Bath Towel"),
        "RIMZIM PRINTED 72CMXC1.44M ASST AW26": ("Rimzim Printed", "Bath Towel"),
        "SUPER ULTRX DYED R4 SET ASST12 AW26": ("Super Ultrx", "Towel Set"),
        "TULIP DYED 60CM X 1.2M ASST12 AW26": ("Tulip", "Ladies Towel"),
        "TULIP 90CM X1.8M WHITE AW26": ("Tulip", "Pool Towel"),
    }
    for code, (brand, size) in cases.items():
        e = enrich_bd_product(code)
        assert e["matched"] is True, code
        assert e["collection"] == brand, (code, e)
        assert e["product_type"] == size, (code, e)


def test_balaji_towel_fo_so_match_lab():
    fo = Path(
        r"G:\My Drive\2026-2027\Oder Management\AW26 order\Towel\Balaji haryana.xlsx"
    )
    so = Path(
        r"G:\My Drive\2026-2027\Oder Management\AW26 order\Towel\SO"
        r"\fwdrfa0381towelordersjun26forcashpayment.zip"
    )
    if not fo.is_file() or not so.is_file():
        import pytest

        pytest.skip("Balaji towel FO/SO sample files not on this machine")

    result = run_match_lab_files(fo_path=fo, so_path=so, category="Bath")
    assert result.get("success") is True
    counts = (result.get("match") or {}).get("counts") or {}
    totals = (result.get("match") or {}).get("totals") or {}
    assert counts.get("MISSING_ON_SO", 1) == 0
    assert counts.get("EXTRA_ON_SO", 1) == 0
    assert counts.get("QTY_MISMATCH", 1) == 0
    assert float(totals.get("delta_qty", 99)) == 0.0
    matched = int(counts.get("MATCH") or 0) + int(counts.get("MATCH_FUZZY_BRAND") or 0)
    assert matched == 21, counts
    assert int(counts.get("VALUE_MISMATCH") or 0) == 0, counts

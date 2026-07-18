from app.fiscal_year import fiscal_year_sort_key, normalize_fiscal_year


def test_normalize_fiscal_year_variants():
    assert normalize_fiscal_year("2024-25") == "2024-2025"
    assert normalize_fiscal_year("24-2025") == "2024-2025"
    assert normalize_fiscal_year("2024 2025") == "2024-2025"
    assert normalize_fiscal_year("2024-2025") == "2024-2025"


def test_fiscal_year_sort_key_ascending():
    years = ["2025-2026", "2024-25", "2023-24"]
    years.sort(key=fiscal_year_sort_key)
    assert years == ["2023-24", "2024-25", "2025-2026"]

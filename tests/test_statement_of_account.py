"""Statement of Account from SAP GL ledger."""

from __future__ import annotations

from pathlib import Path

from app.services.statement_of_account import generate_statement_of_account_xlsx


def test_choice_corner_gl_matches_sample_summary():
    path = Path(r"G:\My Drive\2026-2027\ledger\Choice Corner GL UPTO 13 Aug 2026.xls")
    if not path.exists():
        import pytest

        pytest.skip("Choice Corner GL sample not on this machine")
    _, meta = generate_statement_of_account_xlsx(path.read_bytes(), path.name)
    assert meta["account_no"] == "3220108"
    assert meta["line_count"] == 134
    assert abs(meta["total_sales"] - 8_259_053.17) < 0.05
    assert abs(meta["total_payments"] - 8_178_240.24) < 0.05
    assert abs(meta["total_credit_notes"] - 138_930.49) < 0.05
    assert abs(meta["closing_balance"] - (-58_117.56)) < 0.05

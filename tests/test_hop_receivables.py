"""Receivables come from the real sale bills (made-up company)."""
from __future__ import annotations

from app import hop_ops
from app.hop_db import connect
from app.hop_schema import HOP_WORKSPACE_ID as WS
from tests.test_hop_reports import co  # noqa: F401  (fixture)


def test_receivables_from_sale_bills(co):
    with connect(co["path"]) as conn:
        bal = conn.execute("SELECT SUM(balance_amount) FROM hop_party_transactions WHERE workspace_id=? AND txn_type=1 "
                           "AND (deleted_at IS NULL OR trim(deleted_at)='')", (WS,)).fetchone()[0] or 0
        rep = hop_ops.report_receivables(conn, WS)
        assert round(sum(rep["ageing"].values()), 2) == round(bal, 2)
        if bal > 0:
            assert rep["invoices"] and rep["invoices"][0]["invoice_no"] and rep["top_customers"][0]["customer"] == "Hotel A"

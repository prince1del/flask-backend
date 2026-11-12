"""Distributor Hisaab: order vs received vs billed, FIFO allocation, alerts (synthetic data)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from app.services.distributor_ledger import build_ledger


class _Db:
    def __init__(self, status):
        self._status = status

    def list_distributor_category_payment_status(self, user_id):
        return self._status


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE fo_so_match_runs (id INTEGER PRIMARY KEY, user_id INTEGER, filled_order_id INTEGER,
            distributor_id INTEGER, distributor_name TEXT, category TEXT, season TEXT,
            so_net_amount REAL, rows_json TEXT, created_at TEXT);
        CREATE TABLE order_lifecycle_tracking (tracking_id INTEGER PRIMARY KEY, distributor_id INTEGER,
            order_ref_no TEXT, commercial_invoice_parsed TEXT, workspace_id TEXT,
            commercial_invoice_file_reference TEXT, commercial_invoice_drive_file_id TEXT);
        CREATE TABLE order_fulfillment_items (id INTEGER PRIMARY KEY, order_lifecycle_id INTEGER,
            ci_qty REAL, ci_value REAL);
        CREATE TABLE master_distributors (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, firm_name TEXT);
        INSERT INTO master_distributors VALUES (7, 1, 'Acme', 'Acme');
        """
    )
    rows = [{"so_breakdown": [{"so_number": "102870001", "qty": 100, "net": 100000, "gst": 5000, "total": 105000}]}]
    conn.execute(
        "INSERT INTO fo_so_match_runs VALUES (1, 1, 9, 7, 'Acme', 'Bed', 'AW26', 100000, ?, '2026-08-01')",
        (json.dumps(rows),),
    )
    for tid, inv, day, taxable in ((1, "CI-1", "2026-08-10", 40000.0), (2, "CI-2", "2026-08-20", 20000.0)):
        parsed = {"header": {"invoice_no": inv, "invoice_date": day, "order_ref_no": "102870001"},
                  "totals": {"qty": 40, "taxable_amount": taxable, "invoice_total": taxable * 1.05}}
        conn.execute(
            "INSERT INTO order_lifecycle_tracking VALUES (?, 7, '102870001', ?, 'ws', 'f.pdf', NULL)",
            (tid, json.dumps(parsed)),
        )
    conn.commit()
    return conn


def _status(deposits):
    return [{
        "distributor_id": 7, "distributor_name": "Acme",
        "seasons": [{"season": "AW26", "categories": [{
            "category": "Bed", "so_total": 105000.0, "cd_percent": 3.0, "bill_after_cd": 102000.0,
            "tds_applicable": True, "tds_percent": 0.1, "tds_amount": 105.0,
            "paid_total": sum(d["amount"] for d in deposits), "deposits": deposits,
        }]}],
    }]


def test_ledger_values_cis_like_the_order_and_allocates_oldest_first():
    deps = [{"amount": 30000.0, "payment_date": "2026-08-05"}, {"amount": 30000.0, "payment_date": "2026-08-25"}]
    out = build_ledger(_Db(_status(deps)), _conn(), user_id=1, workspace_id="ws", today=date(2026, 9, 1))
    cat = out["distributors"][0]["categories"][0]
    # CI-1: gross 42,000 − CD 3% of 40,000 (1,200) − TDS 0.1% of 42,000 (42) = 40,758
    assert [c["net"] for c in cat["cis"]] == [40758.0, 20379.0]
    assert cat["order_payable"] == 101895.0
    assert cat["received"] == 60000.0 and cat["billed"] == 61137.0
    assert cat["unbilled_advance"] == -1137.0  # billed more than received
    assert cat["payment_due"] == 41895.0
    assert [c["status"] for c in cat["cis"]] == ["paid", "part_paid"]
    assert cat["cis"][1]["uncovered"] == 1137.0
    assert cat["advance_not_used_by_ci"] == 0.0
    assert any("billed 1,137 more than received" in a["text"] for a in out["distributors"][0]["alerts"])


def test_extra_payment_and_unbilled_advance_alerts():
    deps = [{"amount": 102000.0, "payment_date": "2026-08-01"}]
    out = build_ledger(_Db(_status(deps)), _conn(), user_id=1, workspace_id="ws", today=date(2026, 9, 1))
    d = out["distributors"][0]
    cat = d["categories"][0]
    assert cat["payment_due"] == -105.0  # paid 105 extra
    assert cat["unbilled_advance"] == round(102000 - 61137, 2)
    assert cat["advance_not_used_by_ci"] == round(102000 - 61137, 2)
    texts = " | ".join(a["text"] for a in d["alerts"])
    assert "paid 105 extra" in texts and "CI still to be raised" in texts
    assert out["totals"]["payment_due"] == -105.0

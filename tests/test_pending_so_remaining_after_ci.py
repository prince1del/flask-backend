"""Pending SO achievement: remaining unbilled after partial CI."""

from __future__ import annotations

import json
import sqlite3

from app.services.fo_so_match_db import (
    remaining_so_net_after_ci,
    sum_pending_so_net_for_user,
)


def test_remaining_so_net_qty_half():
    # SO 10, half qty billed → remaining 5
    assert remaining_so_net_after_ci(10.0, 100.0, 50.0, 0.0, has_ci=True) == 5.0


def test_remaining_so_net_fully_billed_qty():
    assert remaining_so_net_after_ci(10.0, 100.0, 100.0, 0.0, has_ci=True) == 0.0


def test_remaining_so_net_value_fallback():
    assert remaining_so_net_after_ci(10.0, 0.0, 0.0, 5.0, has_ci=True) == 5.0


def test_remaining_so_net_no_ci():
    assert remaining_so_net_after_ci(10.0, 100.0, 0.0, 0.0, has_ci=False) == 10.0


def _setup_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE fo_so_match_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filled_order_id INTEGER,
            distributor_id INTEGER,
            distributor_name TEXT,
            category TEXT,
            season TEXT,
            so_net_amount REAL,
            rows_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE master_distributors (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            name TEXT,
            firm_name TEXT
        );
        CREATE TABLE order_lifecycle_tracking (
            tracking_id INTEGER PRIMARY KEY,
            distributor_id INTEGER,
            order_ref_no TEXT,
            commercial_invoice_parsed TEXT,
            commercial_invoice_file_reference TEXT,
            commercial_invoice_drive_file_id TEXT
        );
        CREATE TABLE order_fulfillment_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_lifecycle_id INTEGER,
            ci_qty REAL,
            ci_value REAL
        );
        """
    )
    return conn


def test_sum_pending_keeps_half_after_partial_ci():
    conn = _setup_conn()
    so_number = "102876251"
    rows = [
        {
            "so_breakdown": [
                {
                    "so_number": so_number,
                    "qty": 100.0,
                    "net": 10.0,
                    "gst": 0.0,
                    "total": 10.0,
                }
            ]
        }
    ]
    conn.execute(
        """
        INSERT INTO fo_so_match_runs
        (user_id, filled_order_id, distributor_id, so_net_amount, rows_json, created_at)
        VALUES (1, 9, 1, 10.0, ?, '2026-06-01')
        """,
        (json.dumps(rows),),
    )
    conn.execute(
        "INSERT INTO master_distributors (id, user_id, name) VALUES (1, 1, 'Acme')"
    )
    parsed = {
        "header": {"invoice_no": "CI-1", "total_pieces": 50, "taxable_amount": 5.0},
        "totals": {},
        "line_items": [],
    }
    conn.execute(
        """
        INSERT INTO order_lifecycle_tracking
        (tracking_id, distributor_id, order_ref_no, commercial_invoice_parsed,
         commercial_invoice_file_reference, commercial_invoice_drive_file_id)
        VALUES (1, 1, ?, ?, 'ci.pdf', NULL)
        """,
        (so_number, json.dumps(parsed)),
    )
    conn.commit()

    # Old bug would return 0 (whole SO dropped). Now remaining = 5.
    assert sum_pending_so_net_for_user(conn, 1) == 5.0


def test_sum_pending_zero_when_fully_billed():
    conn = _setup_conn()
    so_number = "102876999"
    rows = [
        {
            "so_breakdown": [
                {
                    "so_number": so_number,
                    "qty": 40.0,
                    "net": 8.0,
                    "gst": 0.0,
                    "total": 8.0,
                }
            ]
        }
    ]
    conn.execute(
        """
        INSERT INTO fo_so_match_runs
        (user_id, filled_order_id, distributor_id, so_net_amount, rows_json, created_at)
        VALUES (1, 2, 1, 8.0, ?, '2026-06-01')
        """,
        (json.dumps(rows),),
    )
    conn.execute(
        "INSERT INTO master_distributors (id, user_id, name) VALUES (1, 1, 'Acme')"
    )
    parsed = {
        "header": {"invoice_no": "CI-2", "total_pieces": 40, "taxable_amount": 8.0},
    }
    conn.execute(
        """
        INSERT INTO order_lifecycle_tracking
        (tracking_id, distributor_id, order_ref_no, commercial_invoice_parsed,
         commercial_invoice_file_reference, commercial_invoice_drive_file_id)
        VALUES (2, 1, ?, ?, 'ci2.pdf', NULL)
        """,
        (so_number, json.dumps(parsed)),
    )
    conn.commit()
    assert sum_pending_so_net_for_user(conn, 1) == 0.0


def test_sum_pending_full_when_no_ci():
    conn = _setup_conn()
    so_number = "102876111"
    rows = [
        {
            "so_breakdown": [
                {
                    "so_number": so_number,
                    "qty": 20.0,
                    "net": 12.0,
                    "gst": 0.0,
                    "total": 12.0,
                }
            ]
        }
    ]
    conn.execute(
        """
        INSERT INTO fo_so_match_runs
        (user_id, filled_order_id, distributor_id, so_net_amount, rows_json, created_at)
        VALUES (1, 3, 1, 12.0, ?, '2026-06-01')
        """,
        (json.dumps(rows),),
    )
    conn.execute(
        "INSERT INTO master_distributors (id, user_id, name) VALUES (1, 1, 'Acme')"
    )
    conn.commit()
    assert sum_pending_so_net_for_user(conn, 1) == 12.0

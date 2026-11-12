"""Pending SO list (server-side): open + partly billed SOs with remaining share."""

from __future__ import annotations

import json

from app.services.fo_so_match_db import list_pending_so_for_user, sum_pending_so_net_for_user
from tests.test_pending_so_remaining_after_ci import _setup_conn


def _run(conn, run_id, so_number, qty, net, *, fo_id, dist_id=1, brand="Aster", size="DB BS"):
    rows = [
        {
            "brand": brand,
            "size": size,
            "so_breakdown": [
                {"so_number": so_number, "qty": qty, "net": net, "gst": 0.0, "total": net}
            ],
        }
    ]
    conn.execute(
        """
        INSERT INTO fo_so_match_runs
        (id, user_id, filled_order_id, distributor_id, distributor_name, category, season,
         so_net_amount, rows_json, created_at)
        VALUES (?, 1, ?, ?, 'Run label', 'Bed', 'AW26', ?, ?, '2026-08-01')
        """,
        (run_id, fo_id, dist_id, net, json.dumps(rows)),
    )


def _ci(conn, tid, so_number, pieces, taxable):
    parsed = {
        "header": {"invoice_no": f"CI-{tid}", "total_pieces": pieces, "taxable_amount": taxable},
        "totals": {},
        "line_items": [],
    }
    conn.execute(
        """
        INSERT INTO order_lifecycle_tracking
        (tracking_id, distributor_id, order_ref_no, commercial_invoice_parsed,
         commercial_invoice_file_reference, commercial_invoice_drive_file_id)
        VALUES (?, 1, ?, ?, 'ci.pdf', NULL)
        """,
        (tid, so_number, json.dumps(parsed)),
    )


def _conn_with_three_sos():
    conn = _setup_conn()
    conn.execute("INSERT INTO master_distributors (id, user_id, name, firm_name) VALUES (1, 1, 'Ram', 'Shri Ram & Co')")
    _run(conn, 1, "102876001", 100.0, 1000.0, fo_id=1)  # partly billed
    _run(conn, 2, "102876002", 50.0, 500.0, fo_id=2)  # fully billed
    _run(conn, 3, "102876003", 10.0, 200.0, fo_id=3)  # no CI
    _ci(conn, 1, "102876001", 40, 400.0)
    _ci(conn, 2, "102876002", 50, 500.0)
    conn.commit()
    return conn


def test_partial_ci_keeps_remaining_and_full_ci_is_dropped():
    conn = _conn_with_three_sos()
    items = {i["so_number"]: i for i in list_pending_so_for_user(conn, 1)}

    assert set(items) == {"102876001", "102876003"}
    part = items["102876001"]
    assert part["status"] == "partial"
    assert part["pending_net"] == 600.0
    assert part["pending_qty"] == 60.0
    assert part["articles_prorated"] is True
    assert part["articles"][0]["pending_net"] == 600.0
    assert part["distributor_name"] == "Shri Ram & Co"

    open_so = items["102876003"]
    assert open_so["status"] == "open"
    assert open_so["pending_net"] == 200.0
    assert open_so["articles"][0]["name"] == "Aster · DB BS"


def test_list_total_matches_pending_achievement_total():
    conn = _conn_with_three_sos()
    items = list_pending_so_for_user(conn, 1)
    assert round(sum(i["pending_net"] for i in items), 2) == sum_pending_so_net_for_user(conn, 1)


def test_other_users_sos_are_not_listed():
    conn = _conn_with_three_sos()
    assert list_pending_so_for_user(conn, 2) == []

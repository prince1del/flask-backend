"""
Verifies workspace isolation at the database-function level for the 4
tables that Phase 2 will build live API routes on top of:
  - primary_sales, secondary_sales (sell-in/sell-out)
  - targets_achievements (manual target/achievement entry)
  - order_lifecycle_tracking (Order -> SO -> CI -> Dispatch -> Payment)

No live routes call these yet, so these tests exercise the
CentralizedDB functions directly rather than going through HTTP —
this proves the database foundation is workspace-safe ahead of the
Phase 2 routes being built.
"""
import csv
import io

import pytest

from centralized_db_system.db import CentralizedDB


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "phase2_tables_test.sqlite3"
    return CentralizedDB(str(db_path))


def test_primary_sales_isolated_by_workspace(db):
    db.record_primary_sales(
        {"distributor_id": 1, "invoice_no": "INV-WS1", "quantity": 100, "amount": 5000},
        workspace_id="ws-1",
    )
    db.record_primary_sales(
        {"distributor_id": 1, "invoice_no": "INV-WS2", "quantity": 200, "amount": 9000},
        workspace_id="ws-2",
    )

    summary_ws1 = db.get_sales_flow_summary(workspace_id="ws-1")
    summary_ws2 = db.get_sales_flow_summary(workspace_id="ws-2")

    assert summary_ws1["primary_volume"] == 100
    assert summary_ws2["primary_volume"] == 200


def test_secondary_sales_isolated_by_workspace(db, tmp_path):
    csv_ws1 = tmp_path / "secondary_ws1.csv"
    csv_ws1.write_text(
        "distributor_id,retailer_id,invoice_no,sale_date,quantity,amount\n"
        "1,1,INV-SEC-WS1,2026-07-01,50,2500\n"
    )
    csv_ws2 = tmp_path / "secondary_ws2.csv"
    csv_ws2.write_text(
        "distributor_id,retailer_id,invoice_no,sale_date,quantity,amount\n"
        "1,1,INV-SEC-WS2,2026-07-01,75,3750\n"
    )

    db.bulk_upload_secondary_sales(str(csv_ws1), workspace_id="ws-1")
    db.bulk_upload_secondary_sales(str(csv_ws2), workspace_id="ws-2")

    summary_ws1 = db.get_sales_flow_summary(workspace_id="ws-1")
    summary_ws2 = db.get_sales_flow_summary(workspace_id="ws-2")

    assert summary_ws1["secondary_volume"] == 50
    assert summary_ws2["secondary_volume"] == 75


def test_targets_achievements_isolated_by_workspace(db, tmp_path):
    csv_ws1 = tmp_path / "targets_ws1.csv"
    csv_ws1.write_text(
        "year,month,distributor_id,zone,target_amount,achievement_amount\n"
        "2026,July,1,North,100000,80000\n"
    )
    csv_ws2 = tmp_path / "targets_ws2.csv"
    csv_ws2.write_text(
        "year,month,distributor_id,zone,target_amount,achievement_amount\n"
        "2026,July,1,South,200000,150000\n"
    )

    db.bulk_upload_targets_achievements(str(csv_ws1), workspace_id="ws-1")
    db.bulk_upload_targets_achievements(str(csv_ws2), workspace_id="ws-2")

    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        rows_ws1 = conn.execute(
            "SELECT zone, target_amount FROM targets_achievements WHERE workspace_id = ?",
            ("ws-1",),
        ).fetchall()
        rows_ws2 = conn.execute(
            "SELECT zone, target_amount FROM targets_achievements WHERE workspace_id = ?",
            ("ws-2",),
        ).fetchall()

    assert len(rows_ws1) == 1
    assert rows_ws1[0] == ("North", 100000.0)
    assert len(rows_ws2) == 1
    assert rows_ws2[0] == ("South", 200000.0)


def test_order_lifecycle_tracking_isolated_by_workspace(db):
    tracking_a = db.create_order_lifecycle_tracking(
        order_ref_no="ORD-WS1-001",
        distributor_id=1,
        order_received_date="2026-07-01",
        payment_status="PENDING",
        workspace_id="ws-1",
    )
    tracking_b = db.create_order_lifecycle_tracking(
        order_ref_no="ORD-WS2-001",
        distributor_id=1,
        order_received_date="2026-07-02",
        payment_status="PAID",
        workspace_id="ws-2",
    )

    # ws-1 must be able to fetch its own record
    own = db.get_order_lifecycle_tracking(tracking_a, workspace_id="ws-1")
    assert own is not None
    assert own["order_ref_no"] == "ORD-WS1-001"

    # ws-1 must NOT be able to fetch ws-2's record by tracking_id
    cross_workspace_attempt = db.get_order_lifecycle_tracking(tracking_b, workspace_id="ws-1")
    assert cross_workspace_attempt is None, (
        "ws-1 was able to read ws-2's order lifecycle tracking record — "
        "workspace isolation is broken"
    )

    # ws-2 can still read its own record
    own_b = db.get_order_lifecycle_tracking(tracking_b, workspace_id="ws-2")
    assert own_b is not None
    assert own_b["order_ref_no"] == "ORD-WS2-001"

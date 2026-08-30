"""Smoke tests for distributor payment collection helpers."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest

from centralized_db_system.db import CentralizedDB
from app.services import fo_so_match_db as matchdb


class PaymentCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = CentralizedDB(self.path)
        # Force schema creation used by app startup paths.
        self.db.list_order_lifecycle_tracking(workspace_id="default", limit=1)

    def tearDown(self) -> None:
        try:
            os.remove(self.path)
        except OSError:
            pass

    def test_status_mapping(self) -> None:
        self.assertEqual(self.db._payment_status_from_amounts(450_000, 0), "DUE")
        self.assertEqual(self.db._payment_status_from_amounts(450_000, 300_000), "PARTIAL")
        self.assertEqual(self.db._payment_status_from_amounts(450_000, 450_000), "PAID")

    def test_add_and_list_payment(self) -> None:
        tid = self.db.create_order_lifecycle_tracking(
            order_ref_no="SO-CHOICE-1",
            distributor_id=1,
            sales_order_file_reference="dummy.pdf",
            sales_order_parsed='{"header":{"invoice_total":450000}}',
            workspace_id="default",
        )
        # Ensure distributor name join works even without master row.
        entry = self.db.add_distributor_payment_entry(
            workspace_id="default",
            distributor_id=1,
            tracking_id=tid,
            amount=300_000,
            payment_date="2026-06-11",
            note="first deposit",
        )
        self.assertEqual(entry["amount"], 300_000)
        board = self.db.list_distributor_payment_collection("default")
        self.assertTrue(board)
        order = board[0]["orders"][0]
        self.assertEqual(order["so_bill_amount"], 450_000)
        self.assertEqual(order["paid_amount"], 300_000)
        self.assertEqual(order["outstanding"], 150_000)
        self.assertEqual(order["payment_status"], "PARTIAL")

    def test_category_payment_dedupes_reuploaded_so_match_runs(self) -> None:
        """Same FO rematched twice must not double SO total / recovery."""
        user_id = 42
        dist_id = 7
        bill_rows = json.dumps(
            [
                {
                    "so_breakdown": [
                        {
                            "so_number": "SO-1",
                            "qty": 1.0,
                            "net": 1_000_000.0,
                            "gst": 50_000.0,
                            "total": 1_050_000.0,
                        }
                    ]
                }
            ]
        )
        with sqlite3.connect(self.path) as conn:
            matchdb.ensure_schema(conn)
            for so_net in (1_000_000.0, 1_000_000.0, 1_000_000.0):
                conn.execute(
                    """
                    INSERT INTO fo_so_match_runs (
                        user_id, filled_order_id, distributor_id, distributor_name,
                        category, season, so_net_amount, rows_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (user_id, 99, dist_id, "Bernina", "Bed", "AW26", so_net, bill_rows),
                )
            conn.commit()

        board = self.db.list_distributor_category_payment_status(user_id)
        self.assertEqual(len(board), 1)
        cat = board[0]["seasons"][0]["categories"][0]
        # Latest run only — bill incl. GST (1_050_000), not net (1_000_000).
        self.assertEqual(cat["so_total"], 1_050_000.0)
        self.assertEqual(cat["outstanding"], 1_050_000.0)

    def test_fy_so_achievement_uses_order_desk_match_not_filled_orders(self) -> None:
        """Achievement SO channel must follow matched SO, not FO ex-mill uploads."""
        user_id = 55
        bill_rows = json.dumps(
            [
                {
                    "so_breakdown": [
                        {
                            "so_number": "SO-PARTIAL",
                            "qty": 50.0,
                            "net": 500_000.0,
                            "gst": 0.0,
                            "total": 500_000.0,
                        }
                    ]
                }
            ]
        )
        with sqlite3.connect(self.path) as conn:
            matchdb.ensure_schema(conn)
            import filled_orders_db as fodb

            fodb.ensure_schema(conn)
            fo_id = fodb.create_filled_order(
                conn,
                user_id,
                1,
                "Bernina",
                "Bed",
                "AW26",
                source_filename="fo100.xlsx",
                quantity_column_used="Qty",
                quantity_unit_used="pieces",
                total_lines=1,
                matched_lines=1,
                unmatched_lines=0,
                flagged_lines=0,
            )
            fodb.insert_filled_order_item(
                conn,
                fo_id,
                {
                    "item_key": "A|B",
                    "brand": "A",
                    "size": "B",
                    "product_type": "Bedsheet",
                    "raw_qty_value": 100.0,
                    "final_piece_qty": 100.0,
                    "ex_mill_price": 10_000.0,
                    "matched": True,
                    "detected_unit": "pieces",
                    "is_clean_bale_multiple": True,
                },
            )
            conn.execute(
                """
                INSERT INTO fo_so_match_runs (
                    user_id, filled_order_id, distributor_id, distributor_name,
                    category, season, so_net_amount, rows_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-08-15')
                """,
                (user_id, fo_id, 1, "Bernina", "Bed", "AW26", 500_000.0, bill_rows),
            )
            conn.commit()
            fo_rupees = conn.execute(
                "SELECT COALESCE(SUM(foi.final_piece_qty * foi.ex_mill_price), 0) "
                "FROM filled_order_items foi "
                "JOIN filled_orders fo ON fo.id = foi.filled_order_id "
                "WHERE fo.user_id = ?",
                (user_id,),
            ).fetchone()[0]
            self.assertEqual(float(fo_rupees), 1_000_000.0)
            self.assertEqual(
                matchdb.sum_deduped_so_net_for_user(
                    conn, user_id, date_from="2026-04-01", date_to="2027-03-31"
                ),
                500_000.0,
            )

        lakhs = self.db.sum_so_value_for_fy(user_id, "2026-2027")
        self.assertAlmostEqual(lakhs, 5.0)


if __name__ == "__main__":
    unittest.main()

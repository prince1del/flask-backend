"""Smoke tests for distributor payment collection helpers."""

from __future__ import annotations

import os
import tempfile
import unittest

from centralized_db_system.db import CentralizedDB


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


if __name__ == "__main__":
    unittest.main()

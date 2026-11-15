"""HoP Vyapar parity — products HSN, manual sales/purchase docs, banking, GST, books."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.hop_db import connect, create_customer
from app.hop_doc_preview import (
    MANUAL_TXN_TYPES,
    create_manual_party_document,
    delete_manual_party_document,
    update_manual_party_document,
)
from app.hop_ops import create_product, create_vendor, list_products, update_product
from app.hop_schema import HOP_WORKSPACE_ID, ensure_hop_schema
from app import hop_books


class HopVyaparParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "hop_parity.sqlite3")
        ensure_hop_schema(self.db)
        self.ws = HOP_WORKSPACE_ID

    def tearDown(self):
        self.tmp.cleanup()

    def test_product_hsn_create_and_update(self):
        with connect(self.db) as conn:
            row = create_product(
                conn,
                self.ws,
                {
                    "name": "Linen Curtain",
                    "code": "LN-01",
                    "selling_price": 1000,
                    "purchase_price": 700,
                    "gst_pct": 12,
                    "hsn": "6303",
                    "stock_qty": 5,
                },
            )
            self.assertEqual(row.get("hsn"), "6303")
            self.assertEqual(float(row.get("gst_pct") or 0), 12)
            updated = update_product(conn, self.ws, row["id"], {"hsn": "6304", "gst_pct": 5})
            self.assertEqual(updated.get("hsn"), "6304")
            listed = list_products(conn, self.ws)
            self.assertEqual(listed[0]["hsn"], "6304")

    def test_manual_sale_invoice_and_payment_in(self):
        with connect(self.db) as conn:
            cust = create_customer(conn, self.ws, {"company": "Hotel Demo", "city": "Delhi"})
            inv = create_manual_party_document(
                conn,
                self.ws,
                {
                    "txn_type": 1,
                    "customer_id": cust["id"],
                    "txn_date": "2026-11-15",
                    "lines": [
                        {"item_name": "Sofa", "qty": 2, "unit": "NOS", "rate": 5000, "tax_pct": 18, "hsn": "9401"}
                    ],
                },
            )
            self.assertIn(1, MANUAL_TXN_TYPES)
            self.assertTrue(inv["txn_number"])
            self.assertGreater(float(inv["total_amount"]), 0)
            self.assertGreater(float(inv["balance_amount"]), 0)
            self.assertLess(int(inv["source_txn_id"]), 0)

            pay = create_manual_party_document(
                conn,
                self.ws,
                {
                    "txn_type": 3,
                    "customer_id": cust["id"],
                    "txn_date": "2026-11-15",
                    "amount": 1000,
                },
            )
            self.assertEqual(float(pay["total_amount"]), 1000)
            self.assertEqual(float(pay["balance_amount"]), 0)

            updated = update_manual_party_document(
                conn,
                self.ws,
                inv["party_txn_id"],
                {
                    "customer_id": cust["id"],
                    "txn_date": "2026-11-15",
                    "lines": [
                        {"item_name": "Sofa", "qty": 1, "unit": "NOS", "rate": 5000, "tax_pct": 18, "hsn": "9401"}
                    ],
                },
            )
            self.assertLess(float(updated["total_amount"]), float(inv["total_amount"]))
            delete_manual_party_document(conn, self.ws, pay["party_txn_id"])

    def test_manual_purchase_bill_vendor(self):
        with connect(self.db) as conn:
            vendor = create_vendor(conn, self.ws, {"company": "Fabric Co", "products": "Linen"})
            bill = create_manual_party_document(
                conn,
                self.ws,
                {
                    "txn_type": 2,
                    "party_type": "vendor",
                    "party_id": vendor["id"],
                    "txn_date": "2026-11-15",
                    "lines": [
                        {"item_name": "Fabric", "qty": 10, "unit": "MTR", "rate": 200, "tax_pct": 5, "hsn": "5208"}
                    ],
                },
            )
            self.assertEqual(bill["party_type"], "vendor")
            self.assertEqual(bill["party_id"], vendor["id"])
            self.assertGreater(float(bill["balance_amount"]), 0)

    def test_banking_stock_gst_and_books(self):
        with connect(self.db) as conn:
            cust = create_customer(conn, self.ws, {"company": "Client A"})
            create_product(conn, self.ws, {"name": "Item A", "hsn": "1234", "gst_pct": 18, "stock_qty": 2})
            create_manual_party_document(
                conn,
                self.ws,
                {
                    "txn_type": 1,
                    "customer_id": cust["id"],
                    "txn_date": "2026-11-15",
                    "lines": [
                        {"item_name": "Item A", "qty": 1, "rate": 1000, "tax_pct": 18, "hsn": "1234"}
                    ],
                },
            )
            acct = hop_books.create_bank_account(
                conn, self.ws, {"name": "HDFC", "account_type": "bank", "opening_balance": 5000}
            )
            self.assertEqual(float(acct["current_balance"]), 5000)
            hop_books.add_bank_entry(
                conn,
                self.ws,
                {"account_id": acct["id"], "entry_type": "in", "amount": 500, "entry_date": "2026-11-15"},
            )
            self.assertEqual(hop_books.cash_available(conn, self.ws), 5500)

            products = hop_books.list_stock(conn, self.ws)
            pid = products[0]["id"]
            j = hop_books.add_stock_journal(
                conn, self.ws, {"product_id": pid, "qty_delta": 3, "reason": "opening"}
            )
            # 2 opening − 1 sold on the invoice above + 3 from the journal
            self.assertEqual(float(j["stock_qty"]), 4)

            hsn = hop_books.gst_hsn_summary(conn, self.ws)
            self.assertGreaterEqual(len(hsn["rows"]), 1)
            g1 = hop_books.gstr1_sales(conn, self.ws)
            self.assertGreaterEqual(g1["invoice_count"], 1)

            tb = hop_books.trial_balance(conn, self.ws)
            self.assertAlmostEqual(tb["total_debit"], tb["total_credit"], places=2)
            pnl = hop_books.profit_and_loss(conn, self.ws)
            self.assertGreater(float(pnl["sales"]), 0)
            bs = hop_books.balance_sheet(conn, self.ws)
            self.assertIn("assets", bs)


if __name__ == "__main__":
    unittest.main()

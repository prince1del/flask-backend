"""House of Prizm — schema + funnel CRUD smoke tests (no dummy seed beyond test writes)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.hop_db import connect, create_customer, create_lead, create_meeting
from app.hop_ops import (
    create_dispatch,
    create_invoice,
    create_order,
    create_payment,
    create_quotation,
    create_sample,
    create_vendor,
    create_vendor_comparison,
    get_project_hub,
    report_funnel,
    report_lead_pipeline,
    update_lead,
)
from app.hop_schema import HOP_WORKSPACE_ID, ensure_hop_schema


class HopFunnelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "hop_test.sqlite3")
        ensure_hop_schema(self.db)
        self.ws = HOP_WORKSPACE_ID

    def tearDown(self):
        self.tmp.cleanup()

    def test_project_centric_funnel_and_hub(self):
        with connect(self.db) as conn:
            customer = create_customer(conn, self.ws, {"company": "Holiday Inn Group", "city": "Delhi"})
            lead = create_lead(
                conn,
                self.ws,
                {
                    "customer_id": customer["id"],
                    "project_name": "Holiday Inn Dwarka",
                    "expected_value": 500000,
                    "stage": "new_lead",
                },
            )
            self.assertTrue(lead.get("project_id"))
            project_id = int(lead["project_id"])

            create_meeting(
                conn,
                self.ws,
                {
                    "project_id": project_id,
                    "customer_id": customer["id"],
                    "scheduled_at": "2026-07-12T10:00:00+00:00",
                    "title": "Site visit",
                    "outcome": "BOQ requested",
                },
            )
            create_sample(
                conn,
                self.ws,
                {"project_id": project_id, "sample_name": "FR Curtain Swatch", "approval_status": "pending"},
            )
            vendor = create_vendor(conn, self.ws, {"company": "Vendor A", "products": "FR Fabric"})
            create_vendor_comparison(
                conn,
                self.ws,
                {
                    "project_id": project_id,
                    "vendor_id": vendor["id"],
                    "product_name": "FR Fabric",
                    "rate": 420,
                    "is_winner": 1,
                },
            )
            quote = create_quotation(
                conn,
                self.ws,
                {"project_id": project_id, "value": 480000, "margin_pct": 22, "status": "sent"},
            )
            self.assertEqual(quote.get("version"), 1)
            order = create_order(
                conn,
                self.ws,
                {
                    "project_id": project_id,
                    "po_number": "PO-1001",
                    "order_value": 480000,
                    "mark_won": True,
                    "production_status": "in_production",
                },
            )
            create_dispatch(
                conn,
                self.ws,
                {"project_id": project_id, "order_id": order["id"], "status": "ready", "tracking_number": "TRK1"},
            )
            invoice = create_invoice(
                conn,
                self.ws,
                {"project_id": project_id, "amount": 480000, "due_date": "2026-08-01"},
            )
            create_payment(conn, self.ws, {"invoice_id": invoice["id"], "amount": 100000, "method": "NEFT"})

            update_lead(conn, self.ws, int(lead["id"]), {"stage": "quotation_sent"})

            hub = get_project_hub(conn, self.ws, project_id)
            self.assertIsNotNone(hub)
            assert hub is not None
            self.assertEqual(hub["project"]["project_name"], "Holiday Inn Dwarka")
            self.assertGreaterEqual(len(hub["quotations"]), 1)
            self.assertGreaterEqual(len(hub["orders"]), 1)
            self.assertGreaterEqual(len(hub["timeline"]), 1)

            pipeline = report_lead_pipeline(conn, self.ws)
            self.assertTrue(pipeline["stages"])
            funnel = report_funnel(conn, self.ws)
            self.assertEqual(len(funnel), 16)


if __name__ == "__main__":
    unittest.main()

"""House of Prizm — isolated schema (project-centric).

Does not touch NEXORA executive tables or existing user rows.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


HOP_WORKSPACE_ID = "house_of_prizm"
HOP_ROLE = "hop_admin"

PROJECT_STAGES = [
    "lead",
    "meeting",
    "requirement",
    "sample",
    "boq",
    "vendor",
    "quotation",
    "negotiation",
    "po",
    "production",
    "dispatch",
    "invoice",
    "payment",
    "after_sales",
    "closed",
    "lost",
]

LEAD_STAGES = [
    "new_lead",
    "contacted",
    "meeting_scheduled",
    "samples_sent",
    "boq_received",
    "quotation_sent",
    "negotiation",
    "po_expected",
    "order_won",
    "lost",
]

# New CRM Deal pipeline — stepwise unlock (Phase 1 legacy fine-grain steps)
DEAL_STEPS = [
    {"id": "lead", "label": "Lead received", "optional": False},
    {"id": "contacting", "label": "Call / Mail / Message / Visit", "optional": False},
    {"id": "appointment", "label": "Appointment", "optional": False},
    {"id": "discovery", "label": "Requirement meeting", "optional": False},
    {"id": "cataloging", "label": "Cataloging", "optional": False},
    {"id": "quotation", "label": "Quotation", "optional": False},
    {"id": "negotiation", "label": "Negotiation", "optional": False},
    {"id": "po_received", "label": "Customer PO", "optional": False},
    {"id": "advance", "label": "Advance payment", "optional": True},
    {"id": "vendor_order", "label": "Vendor order", "optional": False},
    {"id": "inbound", "label": "Vendor dispatch / inbound track", "optional": False},
    {"id": "godown", "label": "Goods at godown", "optional": True},
    {"id": "repack", "label": "Repacking", "optional": True},
    {"id": "outbound", "label": "Ship to client", "optional": False},
    {"id": "delivered", "label": "Delivered", "optional": False},
    {"id": "installation", "label": "Installation", "optional": True},
    {"id": "collection", "label": "Payment follow-up", "optional": False},
    {"id": "closed", "label": "Closed / Won", "optional": False},
]

DEAL_STEP_IDS = [s["id"] for s in DEAL_STEPS]

# Phase 1 Deal CRM — 8 visible pipeline stages (user-facing)
DEAL_STAGES = [
    {"id": "new", "label": "New", "order": 1},
    {"id": "connect", "label": "Connect", "order": 2},
    {"id": "discover", "label": "Discover", "order": 3},
    {"id": "quote", "label": "Quote", "order": 4},
    {"id": "confirm", "label": "Confirm", "order": 5},
    {"id": "fulfil", "label": "Fulfil", "order": 6},
    {"id": "collect", "label": "Collect", "order": 7},
    {"id": "closed", "label": "Closed", "order": 8},
]
DEAL_STAGE_IDS = [s["id"] for s in DEAL_STAGES]

# Side-exit / non-pipeline statuses (status column)
DEAL_PIPELINE_STATUSES = {"open", "won"}  # open = in pipeline; won when closed stage complete
DEAL_SIDE_STATUSES = {
    "lost",
    "on_hold",
    "cancelled",
    "disputed",
    "written_off",
}

# Map legacy 18-step index → 8-stage id
LEGACY_STEP_TO_STAGE = {
    "lead": "new",
    "contacting": "connect",
    "appointment": "connect",
    "discovery": "discover",
    "cataloging": "discover",
    "quotation": "quote",
    "negotiation": "quote",
    "po_received": "confirm",
    "advance": "confirm",
    "vendor_order": "fulfil",
    "inbound": "fulfil",
    "godown": "fulfil",
    "repack": "fulfil",
    "outbound": "fulfil",
    "delivered": "fulfil",
    "installation": "fulfil",
    "collection": "collect",
    "closed": "closed",
}

# Pre-PO loss reasons / post-PO cancellation taxonomy
DEAL_LOSS_REASONS_PRE_PO = [
    "not_interested",
    "price_issue",
    "competitor_selected",
    "no_response",
    "project_postponed",
    "duplicate_enquiry",
    "invalid_enquiry",
]
DEAL_CANCEL_REASONS_POST_PO = [
    "cancelled_by_customer",
    "cancelled_internally",
    "partially_cancelled",
    "on_hold",
    "disputed",
    "bad_debt",
    "written_off",
]

DEAL_ACTIVITY_TYPES = [
    "call",
    "whatsapp",
    "email",
    "visit",
    "appointment",
    "requirement_meeting",
    "measurement_visit",
    "sample_approval_visit",
    "installation_visit",
    "payment_follow_up",
    "internal_note",
    "document_sent",
    "quotation_sent",
]

DEAL_HEALTH_VALUES = ("healthy", "needs_attention", "at_risk", "critical")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    try:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    except Exception:
        # Never fail schema bootstrap on a single additive column.
        pass


def ensure_hop_schema(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS hop_customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                company TEXT NOT NULL,
                contact_person TEXT,
                mobile TEXT,
                email TEXT,
                city TEXT,
                industry TEXT,
                architect TEXT,
                consultant TEXT,
                hotel_brand TEXT,
                annual_potential REAL,
                source TEXT,
                potential_rating TEXT,
                remarks TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hop_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                project_name TEXT NOT NULL,
                customer_id INTEGER,
                client_name TEXT,
                consultant TEXT,
                architect TEXT,
                stage TEXT NOT NULL DEFAULT 'lead',
                expected_value REAL DEFAULT 0,
                probability_pct REAL DEFAULT 0,
                completion_pct REAL DEFAULT 0,
                delay_days INTEGER DEFAULT 0,
                issues TEXT,
                next_milestone TEXT,
                assigned_to TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(customer_id) REFERENCES hop_customers(id)
            );

            CREATE TABLE IF NOT EXISTS hop_leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                lead_number TEXT,
                project_id INTEGER,
                customer_id INTEGER,
                source TEXT,
                assigned_to TEXT,
                priority TEXT,
                expected_value REAL DEFAULT 0,
                probability_pct REAL DEFAULT 0,
                stage TEXT NOT NULL DEFAULT 'new_lead',
                next_follow_up TEXT,
                meeting_notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                lost_at TEXT,
                won_at TEXT,
                FOREIGN KEY(project_id) REFERENCES hop_projects(id),
                FOREIGN KEY(customer_id) REFERENCES hop_customers(id)
            );

            CREATE TABLE IF NOT EXISTS hop_meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                project_id INTEGER,
                lead_id INTEGER,
                title TEXT,
                scheduled_at TEXT NOT NULL,
                location TEXT,
                outcome TEXT,
                next_action TEXT,
                probability_pct REAL,
                expected_order_value REAL,
                status TEXT NOT NULL DEFAULT 'scheduled',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES hop_projects(id),
                FOREIGN KEY(lead_id) REFERENCES hop_leads(id)
            );

            CREATE TABLE IF NOT EXISTS hop_quotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                project_id INTEGER,
                quote_no TEXT,
                customer_id INTEGER,
                quote_date TEXT,
                value REAL DEFAULT 0,
                margin_pct REAL,
                status TEXT NOT NULL DEFAULT 'draft',
                last_follow_up TEXT,
                expected_closure_date TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES hop_projects(id),
                FOREIGN KEY(customer_id) REFERENCES hop_customers(id)
            );

            CREATE TABLE IF NOT EXISTS hop_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                project_id INTEGER,
                po_number TEXT,
                client_name TEXT,
                order_value REAL DEFAULT 0,
                supplier TEXT,
                expected_delivery TEXT,
                production_status TEXT,
                dispatch_status TEXT,
                invoice_status TEXT,
                won_at TEXT,
                lost_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES hop_projects(id)
            );

            CREATE TABLE IF NOT EXISTS hop_dispatches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                order_id INTEGER,
                project_id INTEGER,
                status TEXT NOT NULL DEFAULT 'ready',
                tracking_number TEXT,
                courier TEXT,
                delivery_status TEXT,
                dispatched_at TEXT,
                delivered_at TEXT,
                installation_pending INTEGER NOT NULL DEFAULT 0,
                due_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(order_id) REFERENCES hop_orders(id),
                FOREIGN KEY(project_id) REFERENCES hop_projects(id)
            );

            CREATE TABLE IF NOT EXISTS hop_invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                project_id INTEGER,
                order_id INTEGER,
                invoice_no TEXT,
                customer_id INTEGER,
                amount REAL DEFAULT 0,
                due_date TEXT,
                paid_amount REAL DEFAULT 0,
                balance REAL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES hop_projects(id),
                FOREIGN KEY(order_id) REFERENCES hop_orders(id),
                FOREIGN KEY(customer_id) REFERENCES hop_customers(id)
            );

            CREATE TABLE IF NOT EXISTS hop_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                invoice_id INTEGER,
                amount REAL DEFAULT 0,
                paid_at TEXT NOT NULL,
                method TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(invoice_id) REFERENCES hop_invoices(id)
            );

            CREATE TABLE IF NOT EXISTS hop_party_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                party_type TEXT NOT NULL,
                party_id INTEGER,
                party_name TEXT,
                source_txn_id INTEGER NOT NULL,
                txn_type INTEGER,
                txn_label TEXT,
                txn_number TEXT,
                txn_date TEXT,
                total_amount REAL DEFAULT 0,
                balance_amount REAL DEFAULT 0,
                status_text TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(workspace_id, source_txn_id)
            );

            CREATE TABLE IF NOT EXISTS hop_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                period_label TEXT NOT NULL,
                target_amount REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(workspace_id, period_label)
            );

            CREATE TABLE IF NOT EXISTS hop_vendors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                company TEXT NOT NULL,
                products TEXT,
                gst_no TEXT,
                contact_person TEXT,
                mobile TEXT,
                email TEXT,
                rating REAL,
                payment_terms TEXT,
                lead_time_days INTEGER,
                certificates TEXT,
                quality_rating REAL,
                on_time_pct REAL,
                price_notes TEXT,
                city TEXT,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hop_vendor_comparisons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                project_id INTEGER,
                product_name TEXT,
                vendor_id INTEGER,
                rate REAL,
                lead_time_days INTEGER,
                moq TEXT,
                quality_note TEXT,
                certification TEXT,
                payment_terms TEXT,
                is_winner INTEGER NOT NULL DEFAULT 0,
                recommendation TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES hop_projects(id),
                FOREIGN KEY(vendor_id) REFERENCES hop_vendors(id)
            );

            CREATE TABLE IF NOT EXISTS hop_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                project_id INTEGER,
                customer_id INTEGER,
                sample_name TEXT NOT NULL,
                sent_at TEXT,
                courier TEXT,
                tracking_number TEXT,
                return_status TEXT,
                approval_status TEXT DEFAULT 'pending',
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES hop_projects(id),
                FOREIGN KEY(customer_id) REFERENCES hop_customers(id)
            );

            CREATE TABLE IF NOT EXISTS hop_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                code TEXT,
                name TEXT NOT NULL,
                brand TEXT,
                category TEXT,
                collection TEXT,
                selling_price REAL,
                purchase_price REAL,
                logistics_cost REAL DEFAULT 0,
                gst_pct REAL DEFAULT 0,
                commission_pct REAL DEFAULT 0,
                moq TEXT,
                lead_time_days INTEGER,
                vendor_id INTEGER,
                stock_qty REAL DEFAULT 0,
                specs TEXT,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(vendor_id) REFERENCES hop_vendors(id)
            );

            CREATE TABLE IF NOT EXISTS hop_complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                project_id INTEGER,
                customer_id INTEGER,
                complaint_date TEXT,
                issue TEXT NOT NULL,
                assigned_to TEXT,
                status TEXT DEFAULT 'open',
                resolution_time_hours REAL,
                feedback TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES hop_projects(id),
                FOREIGN KEY(customer_id) REFERENCES hop_customers(id)
            );

            CREATE TABLE IF NOT EXISTS hop_activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                project_id INTEGER,
                customer_id INTEGER,
                entity_type TEXT,
                entity_id INTEGER,
                activity_type TEXT NOT NULL,
                title TEXT,
                detail TEXT,
                activity_at TEXT NOT NULL,
                created_by TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_hop_leads_ws_stage
                ON hop_leads(workspace_id, stage);
            CREATE INDEX IF NOT EXISTS idx_hop_meetings_ws_sched
                ON hop_meetings(workspace_id, scheduled_at);
            CREATE INDEX IF NOT EXISTS idx_hop_quotations_ws_status
                ON hop_quotations(workspace_id, status);
            CREATE INDEX IF NOT EXISTS idx_hop_orders_ws
                ON hop_orders(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_hop_dispatches_ws_status
                ON hop_dispatches(workspace_id, status);
            CREATE INDEX IF NOT EXISTS idx_hop_payments_ws_paid
                ON hop_payments(workspace_id, paid_at);
            CREATE INDEX IF NOT EXISTS idx_hop_party_txn_ws_party
                ON hop_party_transactions(workspace_id, party_type, party_id, txn_date);
            CREATE INDEX IF NOT EXISTS idx_hop_projects_ws
                ON hop_projects(workspace_id, stage);
            CREATE INDEX IF NOT EXISTS idx_hop_customers_ws
                ON hop_customers(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_hop_vendors_ws
                ON hop_vendors(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_hop_samples_ws
                ON hop_samples(workspace_id, project_id);
            CREATE INDEX IF NOT EXISTS idx_hop_products_ws
                ON hop_products(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_hop_complaints_ws
                ON hop_complaints(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_hop_activities_ws
                ON hop_activities(workspace_id, project_id, activity_at);
            CREATE INDEX IF NOT EXISTS idx_hop_vendor_cmp_ws
                ON hop_vendor_comparisons(workspace_id, project_id);

            CREATE TABLE IF NOT EXISTS hop_rate_sheets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                vendor_id INTEGER,
                supplier_name TEXT NOT NULL,
                title TEXT,
                source_type TEXT DEFAULT 'manual',
                quote_date TEXT,
                notes TEXT,
                freight_note TEXT,
                payment_terms TEXT,
                validity_days INTEGER,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(vendor_id) REFERENCES hop_vendors(id)
            );

            CREATE TABLE IF NOT EXISTS hop_rate_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                sheet_id INTEGER NOT NULL,
                product_key TEXT NOT NULL,
                product_name TEXT NOT NULL,
                display_name TEXT,
                category TEXT,
                size TEXT,
                brand TEXT,
                quality TEXT,
                rate REAL NOT NULL,
                gst_pct REAL DEFAULT 5,
                landed_rate REAL,
                qty REAL,
                uom TEXT DEFAULT 'Pcs',
                notes TEXT,
                sort_order INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(sheet_id) REFERENCES hop_rate_sheets(id)
            );

            CREATE INDEX IF NOT EXISTS idx_hop_rate_sheets_ws
                ON hop_rate_sheets(workspace_id, status);
            CREATE INDEX IF NOT EXISTS idx_hop_rate_lines_sheet
                ON hop_rate_lines(workspace_id, sheet_id);
            CREATE INDEX IF NOT EXISTS idx_hop_rate_lines_key
                ON hop_rate_lines(workspace_id, product_key);
            """
        )

        # Additive columns for project-centric CRM (safe on existing DBs)
        for col, ddl in [
            ("customer_type", "TEXT"),
            ("status", "TEXT DEFAULT 'active'"),
            ("assigned_to", "TEXT"),
            ("address", "TEXT"),
            ("gst_no", "TEXT"),
            ("pan", "TEXT"),
            ("billing_name", "TEXT"),
            ("shipping_address", "TEXT"),
            ("state", "TEXT"),
            ("gst_type", "TEXT"),
            ("opening_balance", "REAL"),
            ("opening_balance_date", "TEXT"),
            ("credit_limit", "REAL"),
            ("credit_no_limit", "INTEGER DEFAULT 1"),
            ("additional_fields", "TEXT"),
        ]:
            _ensure_column(conn, "hop_customers", col, ddl)

        for col, ddl in [
            ("address", "TEXT"),
            ("shipping_address", "TEXT"),
            ("billing_name", "TEXT"),
            ("state", "TEXT"),
            ("gst_type", "TEXT"),
            ("opening_balance", "REAL"),
            ("opening_balance_date", "TEXT"),
            ("credit_limit", "REAL"),
            ("credit_no_limit", "INTEGER DEFAULT 1"),
            ("additional_fields", "TEXT"),
        ]:
            _ensure_column(conn, "hop_vendors", col, ddl)

        for col, ddl in [
            ("hotel_name", "TEXT"),
            ("site_address", "TEXT"),
            ("project_value", "REAL DEFAULT 0"),
            ("status", "TEXT DEFAULT 'open'"),
            ("notes", "TEXT"),
        ]:
            _ensure_column(conn, "hop_projects", col, ddl)

        for col, ddl in [
            ("status", "TEXT DEFAULT 'open'"),
            ("discussion", "TEXT"),
            ("competitor", "TEXT"),
            ("expected_budget", "REAL"),
            ("expected_closure_date", "TEXT"),
            ("products_interested", "TEXT"),
        ]:
            _ensure_column(conn, "hop_leads", col, ddl)

        for col, ddl in [
            ("agenda", "TEXT"),
            ("customer_id", "INTEGER"),
            ("follow_up_at", "TEXT"),
            ("notes", "TEXT"),
        ]:
            _ensure_column(conn, "hop_meetings", col, ddl)

        for col, ddl in [
            ("terms", "TEXT"),
            ("payment_terms", "TEXT"),
            ("delivery_terms", "TEXT"),
            ("warranty", "TEXT"),
            ("sales_person", "TEXT"),
            ("notes", "TEXT"),
            ("parent_quote_id", "INTEGER"),
        ]:
            _ensure_column(conn, "hop_quotations", col, ddl)

        for col, ddl in [
            ("customer_id", "INTEGER"),
            ("vendor_id", "INTEGER"),
            ("order_type", "TEXT DEFAULT 'customer_po'"),
            ("status", "TEXT DEFAULT 'open'"),
            ("notes", "TEXT"),
        ]:
            _ensure_column(conn, "hop_orders", col, ddl)

        for col, ddl in [
            ("invoice_no_link", "TEXT"),
            ("eway_bill", "TEXT"),
            ("docket_number", "TEXT"),
            ("pod_received", "INTEGER DEFAULT 0"),
            ("notes", "TEXT"),
        ]:
            _ensure_column(conn, "hop_dispatches", col, ddl)

        for col, ddl in [
            ("status", "TEXT DEFAULT 'open'"),
            ("invoice_date", "TEXT"),
            ("gst_amount", "REAL DEFAULT 0"),
            ("notes", "TEXT"),
            ("source_txn_id", "INTEGER"),
        ]:
            _ensure_column(conn, "hop_invoices", col, ddl)

        for col, ddl in [
            ("customer_id", "INTEGER"),
            ("project_id", "INTEGER"),
            ("reminder_at", "TEXT"),
            ("source_txn_id", "INTEGER"),
        ]:
            _ensure_column(conn, "hop_payments", col, ddl)

        # One Vyapar txn → one HoP invoice / imported payment (safe re-import).
        try:
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_hop_invoices_ws_source_txn
                ON hop_invoices(workspace_id, source_txn_id)
                WHERE source_txn_id IS NOT NULL
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_hop_payments_ws_source_txn
                ON hop_payments(workspace_id, source_txn_id)
                WHERE source_txn_id IS NOT NULL
                """
            )
        except Exception:
            pass

        for col, ddl in [
            ("source_filename", "TEXT"),
            ("source_file_path", "TEXT"),
            ("parse_method", "TEXT"),
            ("parse_warnings", "TEXT"),
        ]:
            _ensure_column(conn, "hop_rate_sheets", col, ddl)

        # Document preview: firm letterhead + per-txn line items (from Vyapar import)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hop_firm_profile (
                workspace_id TEXT PRIMARY KEY,
                firm_name TEXT,
                address TEXT,
                phone TEXT,
                email TEXT,
                gstin TEXT,
                state TEXT,
                pan TEXT,
                bank_name TEXT,
                bank_account TEXT,
                bank_ifsc TEXT,
                bank_holder TEXT,
                logo_url TEXT,
                terms_default TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hop_txn_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                source_txn_id INTEGER NOT NULL,
                line_no INTEGER NOT NULL DEFAULT 0,
                item_name TEXT,
                item_code TEXT,
                description TEXT,
                hsn TEXT,
                qty REAL DEFAULT 0,
                unit TEXT,
                rate REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                tax_pct REAL DEFAULT 0,
                tax_amount REAL DEFAULT 0,
                line_total REAL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(workspace_id, source_txn_id, line_no)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_hop_txn_lines_ws_txn
            ON hop_txn_lines(workspace_id, source_txn_id)
            """
        )

        for col, ddl in [
            ("delivery_terms", "TEXT"),
            ("business_type", "TEXT"),
            ("business_category", "TEXT"),
            ("pincode", "TEXT"),
            ("signature_url", "TEXT"),
        ]:
            _ensure_column(conn, "hop_firm_profile", col, ddl)

        for col, ddl in [
            ("shipping_amount", "REAL DEFAULT 0"),
            ("discount_amount", "REAL DEFAULT 0"),
            ("discount_pct", "REAL DEFAULT 0"),
            ("round_off", "REAL DEFAULT 0"),
            ("doc_terms", "TEXT"),
            ("delivery_terms", "TEXT"),
        ]:
            _ensure_column(conn, "hop_party_transactions", col, ddl)

        # Manual commission + TDS against a tax invoice (Sale Invoice)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hop_commission_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                party_txn_id INTEGER,
                source_txn_id INTEGER,
                invoice_no TEXT,
                party_name TEXT,
                invoice_date TEXT,
                invoice_total REAL DEFAULT 0,
                amount_before_tax REAL DEFAULT 0,
                tax_amount REAL DEFAULT 0,
                commission_pct REAL DEFAULT 0,
                tds_pct REAL DEFAULT 0,
                commission_amount REAL DEFAULT 0,
                tds_amount REAL DEFAULT 0,
                net_commission REAL DEFAULT 0,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(workspace_id, source_txn_id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_hop_commission_ws_date
            ON hop_commission_entries(workspace_id, invoice_date DESC)
            """
        )
        for col, ddl in (
            ("agent_name", "TEXT"),
            ("paid_on", "TEXT"),
            ("agent_party_id", "INTEGER"),
            ("agent_party_type", "TEXT"),
            ("expense_source_txn_id", "INTEGER"),
            ("expense_txn_number", "TEXT"),
            ("expense_line_no", "INTEGER"),
            ("origin", "TEXT"),
            ("payment_status", "TEXT"),
        ):
            _ensure_column(conn, "hop_commission_entries", col, ddl)
        try:
            # Heal legacy rows: expense-linked or dated → paid; else unpaid
            conn.execute(
                """
                UPDATE hop_commission_entries
                SET payment_status = CASE
                  WHEN payment_status IS NOT NULL AND TRIM(payment_status) != '' THEN lower(payment_status)
                  WHEN expense_source_txn_id IS NOT NULL THEN 'paid'
                  WHEN paid_on IS NOT NULL AND TRIM(paid_on) != '' THEN 'paid'
                  ELSE 'unpaid'
                END
                WHERE payment_status IS NULL OR TRIM(payment_status) = ''
                """
            )
        except Exception:
            pass
        try:
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_hop_commission_ws_agent
                ON hop_commission_entries(workspace_id, agent_name, invoice_date DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_hop_commission_ws_expense
                ON hop_commission_entries(workspace_id, expense_source_txn_id, expense_line_no)
                """
            )
        except Exception:
            pass

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS hop_deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                deal_number TEXT,
                title TEXT NOT NULL,
                customer_id INTEGER,
                party_name TEXT,
                source TEXT,
                assigned_to TEXT,
                expected_value REAL DEFAULT 0,
                current_step TEXT NOT NULL DEFAULT 'lead',
                step_index INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'open',
                fulfillment_mode TEXT,
                products_interested TEXT,
                requirement_notes TEXT,
                next_follow_up TEXT,
                advance_amount REAL DEFAULT 0,
                advance_received REAL DEFAULT 0,
                lost_reason TEXT,
                lost_at TEXT,
                closed_at TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(customer_id) REFERENCES hop_customers(id)
            );

            CREATE INDEX IF NOT EXISTS idx_hop_deals_ws
            ON hop_deals(workspace_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS hop_deal_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                deal_id INTEGER NOT NULL,
                step_id TEXT,
                event_type TEXT NOT NULL,
                title TEXT,
                detail TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(deal_id) REFERENCES hop_deals(id)
            );

            CREATE INDEX IF NOT EXISTS idx_hop_deal_events_deal
            ON hop_deal_events(workspace_id, deal_id, id DESC);

            CREATE TABLE IF NOT EXISTS hop_deal_stage_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                deal_id INTEGER NOT NULL,
                from_stage TEXT,
                to_stage TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                reason TEXT,
                changed_by TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(deal_id) REFERENCES hop_deals(id)
            );

            CREATE INDEX IF NOT EXISTS idx_hop_deal_stage_hist
            ON hop_deal_stage_history(workspace_id, deal_id, id DESC);

            CREATE TABLE IF NOT EXISTS hop_deal_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                deal_id INTEGER NOT NULL,
                product_category TEXT,
                product_description TEXT,
                catalogue TEXT,
                design_code TEXT,
                colour TEXT,
                qty REAL DEFAULT 0,
                unit TEXT,
                room_area TEXT,
                notes TEXT,
                line_status TEXT DEFAULT 'pending',
                sort_order INTEGER DEFAULT 0,
                deleted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(deal_id) REFERENCES hop_deals(id)
            );

            CREATE INDEX IF NOT EXISTS idx_hop_deal_lines_deal
            ON hop_deal_lines(workspace_id, deal_id, sort_order);

            CREATE TABLE IF NOT EXISTS hop_deal_activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                deal_id INTEGER NOT NULL,
                activity_type TEXT NOT NULL,
                activity_at TEXT NOT NULL,
                performed_by TEXT,
                contact_person TEXT,
                outcome TEXT,
                related_stage TEXT,
                note TEXT,
                next_action_type TEXT,
                next_action_due TEXT,
                no_follow_up INTEGER DEFAULT 0,
                attachment_name TEXT,
                deleted_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(deal_id) REFERENCES hop_deals(id)
            );

            CREATE INDEX IF NOT EXISTS idx_hop_deal_activities_deal
            ON hop_deal_activities(workspace_id, deal_id, activity_at DESC);

            CREATE TABLE IF NOT EXISTS hop_deal_appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                deal_id INTEGER NOT NULL,
                title TEXT,
                scheduled_at TEXT NOT NULL,
                location TEXT,
                appt_status TEXT NOT NULL DEFAULT 'scheduled',
                outcome TEXT,
                notes TEXT,
                created_by TEXT,
                deleted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(deal_id) REFERENCES hop_deals(id)
            );

            CREATE INDEX IF NOT EXISTS idx_hop_deal_appts_deal
            ON hop_deal_appointments(workspace_id, deal_id, scheduled_at);

            CREATE TABLE IF NOT EXISTS hop_deal_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                deal_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                field_name TEXT,
                old_value TEXT,
                new_value TEXT,
                reason TEXT,
                actor TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(deal_id) REFERENCES hop_deals(id)
            );

            CREATE INDEX IF NOT EXISTS idx_hop_deal_audit
            ON hop_deal_audit_log(workspace_id, deal_id, id DESC);
            """
        )

        # Deal CRM v2 additive columns + migrate legacy 18-step → 8-stage
        for col, ddl in (
            ("current_stage", "TEXT DEFAULT 'new'"),
            ("current_sub_status", "TEXT"),
            ("deal_health", "TEXT DEFAULT 'healthy'"),
            ("priority", "TEXT DEFAULT 'normal'"),
            ("expected_close_date", "TEXT"),
            ("product_category", "TEXT"),
            ("next_action_type", "TEXT"),
            ("next_action_owner", "TEXT"),
            ("next_action_due", "TEXT"),
            ("next_action_note", "TEXT"),
            ("no_action_until", "TEXT"),
            ("created_by", "TEXT"),
            ("deleted_at", "TEXT"),
            ("legacy_step", "TEXT"),
            ("requirement_summary", "TEXT"),
            ("loss_reason_code", "TEXT"),
            ("cancel_reason_code", "TEXT"),
            ("side_exit_remarks", "TEXT"),
            ("side_exit_at", "TEXT"),
            ("competitor", "TEXT"),
            ("revival_date", "TEXT"),
        ):
            _ensure_column(conn, "hop_deals", col, ddl)

        try:
            all_deals = conn.execute(
                "SELECT id, current_step, current_stage, status, legacy_step FROM hop_deals"
            ).fetchall()
            valid_stages = {
                "new", "connect", "discover", "quote", "confirm", "fulfil", "collect", "closed"
            }
            for r in all_deals:
                rid = r[0]
                step = (r[1] or "lead").strip()
                stage = (r[2] or "").strip()
                legacy = r[4]
                mapped = LEGACY_STEP_TO_STAGE.get(step, "new")
                if not stage or stage not in valid_stages:
                    conn.execute(
                        """
                        UPDATE hop_deals
                        SET current_stage=?, legacy_step=COALESCE(NULLIF(legacy_step,''), ?)
                        WHERE id=?
                        """,
                        (mapped, step, rid),
                    )
                elif not legacy:
                    conn.execute(
                        "UPDATE hop_deals SET legacy_step=? WHERE id=? AND (legacy_step IS NULL OR legacy_step='')",
                        (step, rid),
                    )
        except Exception:
            pass

        # Deal requirement lines — extended fields for full-screen editor
        for col, ddl in (
            ("item_name", "TEXT"),
            ("sample_status", "TEXT"),
            ("required_by", "TEXT"),
            ("priority", "TEXT"),
            ("collection", "TEXT"),
            ("composition", "TEXT"),
            ("width", "TEXT"),
            ("finish", "TEXT"),
            ("measurement_notes", "TEXT"),
            ("sample_required", "INTEGER DEFAULT 0"),
            ("customer_remarks", "TEXT"),
        ):
            _ensure_column(conn, "hop_deal_lines", col, ddl)

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS hop_deal_bulk_submissions (
                submission_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                deal_id INTEGER NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_hop_deal_bulk_sub
                ON hop_deal_bulk_submissions(workspace_id, deal_id, created_at DESC);
            """
        )

        # Heal stale product_key / size so Delete & vendor matrix never drift after upgrades
        try:
            from app import hop_ops as _hop_ops

            _hop_ops.sync_all_rate_line_identities(conn)
            _hop_ops.prune_empty_rate_sheets(conn, HOP_WORKSPACE_ID)
        except Exception:
            # Schema ensure must not fail if rate tables are mid-migration
            pass

        # Expand short invoice/doc numbers → HOP/FY/serial (once per ensure)
        try:
            from app.hop_doc_numbers import backfill_full_doc_numbers

            backfill_full_doc_numbers(conn, HOP_WORKSPACE_ID)
        except Exception:
            pass

        conn.commit()
    finally:
        conn.close()

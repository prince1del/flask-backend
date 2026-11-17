"""ⓘ help — "On this screen": what every tab, card, column and button on a HoP screen shows.

Kept apart from hop_help.py so the screen descriptions are easy to keep in step with the screens.
Each line: "Name — what it means". Phone and web show these above "How to use".
"""
from __future__ import annotations

GOV_BILL = ["Pay online link / Send SMS / Make IRN / E-Way Bill — appear on a sale invoice once switched on in Settings → Government & approvals; a paid link marks the bill paid by itself."]
DOC_LIST = [
    "Search box — find a document by number, party or status.",
    "Each card — date and status (top), document number, party name, Amount (full total) and Balance (still to pay / receive).",
    "Count (top right) — how many documents match your search.",
    "+ New (bottom right) — start a new document. Tap a card to open its preview.",
]
PREVIEW = ["Preview — the bill as it prints; from there: Edit, WhatsApp / SMS, Print receipt (Bluetooth printer), PDF, Delete."]

SHOWS: dict[str, list[str]] = {
    "dashboard": [
        "NEXORA Guard (top card) — mistakes AI found in your data; “All clear” means nothing to fix.",
        "Quotations Pending / Sent — price offers waiting for an answer, and offers sent.",
        "Orders Won — quotations that became orders.",
        "Production — orders being made.",
        "Dispatch Due — orders that should go out now.",
        "Outstanding — money customers still owe you.",
        "Collections Today — money received today.",
        "Sales vs Target — this period's sales against your target.",
        "Refresh — loads the latest numbers. Tap any card to open that list.",
    ],
    "parties": [
        "Search — find a party by name, phone or GSTIN.",
        "Each party — name, group (Hotel, Architect…), phone and balance: they owe you, or you owe them.",
        "Add Party — new customer or supplier. Tap a party to edit it or see its bills and statement.",
    ],
    "invoices": DOC_LIST + PREVIEW + GOV_BILL + ["Status — Paid, Partly paid, Unpaid or Overdue."],
    "sale_estimates": DOC_LIST + PREVIEW + ["Status — Open, Converted (became an order / invoice) or Closed."],
    "sale_proforma": DOC_LIST + PREVIEW,
    "payments": [
        "Each card — date, receipt number, customer and amount received.",
        "Linked bills — which invoices this money paid; anything left stays as an advance.",
        "+ New — record money received.",
    ],
    "sale_orders": DOC_LIST + PREVIEW + ["Status — Open, or converted to an invoice / challan."],
    "sale_challan": DOC_LIST + PREVIEW + ["Status — Open (not billed yet) or Converted to an invoice."],
    "sale_returns": DOC_LIST + PREVIEW,
    "commission": DOC_LIST + PREVIEW,
    "purchase_bills": DOC_LIST + PREVIEW + ["Balance — what YOU still owe the supplier on that bill."],
    "purchase_payment_out": [
        "Each card — date, number, supplier and amount paid.",
        "Linked bills — which purchase bills this payment cleared.",
        "+ New — record a payment to a supplier.",
    ],
    "purchase_expenses": [
        "Each card — date, category (Rent, Salary…) or party, amount and how it was paid.",
        "+ New — add an expense; + New category — your own expense heads.",
    ],
    "other_income": [
        "Each card — date, income category (rent received, interest…), amount and where the money went.",
        "+ New — add other income.",
    ],
    "purchase_orders": DOC_LIST + PREVIEW + ["Status — Open or Converted to a purchase bill."],
    "purchase_returns": DOC_LIST + PREVIEW + ["Balance — this return reduces what you owe the supplier."],
    "journal_entries": [
        "Each entry — date, party / account, amount (debit or credit) and your note.",
        "+ New — add an adjustment.",
    ],
    "products": [
        "Search / barcode — find an item by name, code, brand, or by scanning its barcode.",
        "Each item — name, code, sale price, purchase price, GST %, stock and unit; a ⚠ line means something to check.",
        "Margin / profit — what you earn per piece after cost, GST and commission.",
        "+ Product — add an item. Tap an item to edit it (MRP, wholesale, 2nd unit, barcode, service period, custom fields).",
        "Share online catalogue — one link for customers (no login): they pick items and send an order, which arrives as a Sale Order.",
    ],
    "stock": [
        "Each item — quantity in stock now and its unit; low or negative stock stands out.",
        "Stock journal — correct a count (opening, damage, physical count).",
    ],
    "app_updates": [
        "Each date — what changed for you that day.",
        "New + Install now — a newer app is ready to install.",
    ],
    "reminders": [
        "PAYMENTS tab — sale bills with money still due, in 3 groups: Overdue (due date passed), Due today, Due soon. Card: customer, bill no., due date, balance, last reminded.",
        "Payment card buttons — WhatsApp / SMS send your reminder message (you tap Send); Email for customers with an email.",
        "SERVICE tab — items you sold that need service: Service overdue, Due today, Due in 7 days. Card: customer, item, sale date, service date.",
        "Service card buttons — WhatsApp / SMS ask the customer to book a visit; Done after the visit starts the next cycle.",
        "Email today's list / Email backup — sends them to your email right now.",
    ],
    "manufacture": [
        "Raw materials for ONE piece — the raw items and quantity needed to make one finished piece.",
        "Manufacture — how many to make; Check stock & cost shows if raw stock is enough and the cost per piece.",
        "Entries — past manufacturing: date, item, quantity and cost; Undo reverses one.",
    ],
    "godowns": [
        "Godowns — each store / warehouse you added.",
        "Stock by godown — how much of each item is in each godown.",
        "Move — shift stock from one godown to another.",
    ],
    "banking": [
        "Cash in hand — cash balance now.",
        "Bank accounts — each account with its balance; tap one to reconcile it with the statement.",
        "Cheques — received / issued cheques still open; deposit or mark withdrawn.",
        "Bank entries — deposits, withdrawals and transfers.",
    ],
    "gst_reports": [
        "GSTR-1 — your sales for the GST return (B2B, B2C, credit notes).",
        "GSTR-3B — tax to pay for the month.",
        "GSTR-9 — yearly return figures.",
        "HSN summary — sales grouped by HSN code.",
        "Dates + Download — pick the period and download the file for the GST portal.",
    ],
    "books": [
        "Report list — Profit & Loss, Balance Sheet, Cash flow, Day book, Party statement, Sales, Purchase, Stock, GST and more.",
        "Dates — every report works for the period you pick.",
        "✨ AI insights — 3–5 plain points and one action on the report you opened.",
    ],
    "receivables": [
        "Each party — total they owe you and how old it is (by age).",
        "Total — all money to collect.",
    ],
    "overdue_bills": [
        "Each bill — customer, bill no., due date, days late and balance.",
        "Tap a bill — send a payment reminder.",
    ],
    "stock_reorder": [
        "Each item — stock now, how fast it sold in the last 30 days, and how much to order before it runs out.",
    ],
    "app_settings": [
        "Sections — Transaction (numbering, due days, round off), Tax / GST, Print (paper, header, HSN, margins), Sharing (WhatsApp / SMS messages, UPI), Party (groups, loyalty), Reminders (morning email, auto emails, backup email), App lock, Backup.",
        "Saving — switches save at once; text boxes save with Save.",
        "Government & approvals (owner) — E-Invoice / E-Way Bill / GSTIN verify, SMS (DLT) and online payments (your Razorpay): what to apply for, keys (stored locked), Off / Test / Live and a Test button.",
        "Backup — Download backup (locked .kunwar file, opens only in NEXORA), Restore into a new firm, and Backups on server (every 6 hours + before import / wipe / replace, 30 days; Replace this firm needs your password and saves today's data first). Owner only — or staff the owner allowed to download.",
    ],
    "staff_permissions": [
        "Each staff login — name and quick role (Salesperson, Accountant…).",
        "Areas — Sales, Purchase, Money, Items, Reports, Settings: what the person may see or change in each.",
        "Summary — one plain sentence of what this person can do, with warnings for risky choices.",
    ],
    "audit_log": [
        "Each line — when, who (login), what (bill / item), the action (created, edited, deleted) and the old vs new amount.",
        "Search — by bill number or name.",
    ],
    "company_profile": [
        "Firm details — name, GSTIN, state, address, phone, email, PAN.",
        "Bank — account details printed on bills.",
        "Logo & signature — images printed on bills.",
    ],
    "financial_year": ["Each year — open or closed. A closed year's documents cannot be changed."],
    "tally_export": [
        "Period — from / to dates.",
        "Downloads — vouchers for Tally and a day-book CSV.",
    ],
    "loans": ["Each loan — lender, amount, balance left; its entries show EMIs, interest and charges."],
    "fixed_assets": [
        "Each asset — name, cost, date bought, depreciation % and value now.",
        "Run depreciation — this month's depreciation for all assets.",
    ],
    "recycle_bin": ["Each deleted document or item — what it was and when it was deleted; Restore brings it back."],
    "item_settings": [
        "Categories — groups for your items (Curtains, Fabric…).",
        "Units — PCS, MTR, BOX… used on bills.",
    ],
    "print_theme": ["Colours — the accent colour of printed bills; the sample shows how it looks."],
}


from app.hop_help_more import MORE as _MORE  # noqa: E402

for _k, _v in _MORE.items():
    SHOWS[_k] = _v["shows"]

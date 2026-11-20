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


# ── 18-11-2026: what changed on these screens (kept in the Info sheet) ─────────────────────────────
SHOWS["invoices"] = SHOWS["invoices"] + [
    "Date — tap it to pick from the calendar (every date in the app works the same way).",
    "Customer PO no. and PO date — your customer's order number; printed on the bill.",
    "E-way bill no., Vehicle no., Transporter — printed on the bill when filled in.",
    "Time — saved automatically; type one only to change it.",
    "Converted quotation — shows 'Converted to Sale Invoice …', tap it to open that bill.",
    "Earlier payments — after you pick the customer, a Payment-In not yet on any bill shows; tick it to put that money on this bill.",
    "Link a Payment-In — on a saved bill with balance, tap it to put a payment made later on this bill; the balance drops.",
    "Bills on hold — starting a new bill while one is on hold asks first: Resume, Keep on hold & start new, or Delete held & start new.",
]
SHOWS["sale_estimates"] = SHOWS.get("sale_estimates", []) + [
    "Convert → Sale Order / Invoice / Proforma — a quotation can be converted once; after that it links to the new bill.",
]
SHOWS["payments"] = SHOWS["payments"] + ["UTR / Ref no. — for bank or UPI payments (cheque no. for a cheque); printed on the receipt."]
SHOWS["purchase_payment_out"] = SHOWS.get("purchase_payment_out", []) + ["UTR / Ref no. — for bank or UPI payments; printed on the voucher."]
SHOWS["purchase_bills"] = SHOWS["purchase_bills"] + [
    "Link a Payment-Out — on a saved bill with balance, put a payment made earlier or later on this bill.",
    "Copy pending — purchase bills without the supplier's bill photo / PDF are listed at the top; tap one to add it.",
    "Supplier's bill copy — on a saved purchase bill: Camera, Gallery or PDF keeps the supplier's own bill with it (Settings → Transaction switches this on / off).",
    "Supplier bill no. and Bill date — the number printed on the supplier's own bill (our ref no. is separate).",
]
SHOWS["sale_returns"] = SHOWS["sale_returns"] + ["Against invoice — pick the original bill (GST needs it on a credit note)."]
SHOWS["commission"] = [
    "Top — total commission, TDS and net still to pay.",
    "Each card — who gets it, on which bill, commission % and ₹, TDS % and ₹, net, paid or not. Tap → clean copy with Open bill / Edit / Delete.",
    "+ Commission — pick the bill: Due (customer has a usual agent), No commission, or All; search by bill, party or agent.",
    "Use for every bill of this customer — remembers the agent and %; their next bills show as Due.",
    "TDS on commission — section 194H, 2% (it was 5% before Oct 2024).",
]
SHOWS["notes"] = [
    "Search — finds words in the note, its title or party.",
    "Each note — title, text, party, remind date; pinned notes stay on top.",
    "Tap a note — clean view with Edit / Delete / Pin.",
    "Replies — the answers in a note (💬 comment, 📋 plan); write your own reply and tap Send.",
    "Mark as done — the work in the note is finished; done notes show ✅ and go to the bottom (Open again undoes it).",
    "+ Note — write a note; or ask Nexora to write it (you check and Save).",
]
SHOWS["samples"] = SHOWS.get("samples", []) + [
    "New sample — pick the party, add every sample in the send (name, qty, unit), sent date, courier and tracking.",
    "Tap a sample — mark it Approved, Rejected or Returned.",
]
SHOWS["dispatches"] = SHOWS.get("dispatches", []) + [
    "Bill / Challan no. — pick from your sale bills or delivery challans (PO is optional); customer shows on the card.",
    "Tap a dispatch — mark it In transit or Delivered (today's date is stamped).",
]
SHOWS["products"] = SHOWS["products"] + ["Photo — 📷 Camera or 🖼 Gallery in the product form; used in the online catalogue."]
SHOWS["receivables"] = SHOWS["receivables"] + [
    "Tap a customer — only their pending bills are listed; Show all to go back.",
    "Tap a bill — opens the full invoice copy.",
]
SHOWS["dashboard"] = SHOWS["dashboard"] + [
    "Cards — every card opens its screen (Outstanding → Receivables, Collections → Daily activity, Sales vs Target → Targets…).",
    "Cash Available — cash in hand + all bank balances (same as Cash & bank).",
]

SHOWS["bulk_bills"] = [
    "Dates — From / To, or a quick range (this month, last month, this or last financial year).",
    "Include — the kinds of bills to put in the PDF.",
    "Add supplier's bill copies — each purchase bill / expense is followed by the copy saved with it.",
    "Summary — how many bills and copies; a warning when purchase bills have no copy yet.",
    "Print all / Save PDF / Share — one PDF with every bill.",
]

SHOWS["dist_orders"] = [
    "Company — the brand you distribute for (Set company / Change).",
    "Filters — Open, Ordered, SO received, Part billed, Billed, All with counts.",
    "Each order — number, date, amount, pieces, days waiting, its SO and CI; tap to open.",
    "+ SO received — type it or ✨ let AI read the SO PDF; Link CI — put the company's invoice on the SO.",
    "SO without my order — SOs that came without a matching order.",
]
SHOWS["dist_ledger"] = [
    "Totals — Billed, Paid, To pay, Overdue: your account with the company.",
    "Unpaid company invoices — with days overdue; tap to open.",
    "Ledger — every entry with running balance; Pay the company opens Payment-Out.",
]
SHOWS["dist_retailer_goods"] = [
    "Dates — this month, last month, this financial year, or your own.",
    "Each retailer — pieces, amount, bills, last date; tap for item-wise list.",
]
SHOWS["dist_warehouse"] = ["Totals — stock value and out-of-stock count.", "Item list — qty and value; search; What to order / Godowns / Stock entries."]
SHOWS["dist_forecast"] = [
    "Days — look at sales of the last 30 / 90 / 180 days; keep stock for 15 / 30 / 60 days.",
    "Totals — Order now, Order soon, Suggested value.",
    "Each item — stock, on order, sale per day, days left, trend, order qty and order-by date.",
    "✨ Make the suggested order — you check, then a Purchase Order is saved.",
]

SHOWS["dist_articles"] = [
    "✨ Read company price list — Excel read directly, PDF / photo read by AI; preview before saving.",
    "AI check on the master — articles without DP, above MRP, below cost, codes used twice.",
    "Each article — code, name, MRP, DP, price to retailer, margin %, category, season, stock, price changes.",
]
SHOWS["dist_retailer_dues"] = [
    "Totals — to collect and overdue.",
    "✨ Call today — who to call first and why.",
    "Each retailer — due by age (0-30 / 31-60 / 61-90 / 90+), last payment, promise, broken promises; tap for call / WhatsApp / follow-up.",
]
SHOWS["dist_staff"] = ["Dates — from / to.", "Each person — sales, bills, collections, orders, follow-ups, edits, deletes, active days."]
SHOWS["dist_profit"] = [
    "Totals — sales, gross profit, margin, expenses, net profit.",
    "AI check — sales below cost, items without buying price, low margins.",
    "Month by month — sales, gross profit, net.",
    "Tabs — items, retailers, companies, sales below cost (tap to open the bill).",
]
SHOWS["dist_ledger"] = SHOWS["dist_ledger"] + ["✨ Check with the company's ledger — upload their statement; AI lists missing invoices, payments and amount differences."]
SHOWS["dist_orders"] = SHOWS["dist_orders"] + ["✨ SO check — before saving, the SO is compared with your order and the Article Master (items, qty, rate, dates)."]

SHOWS["dist_price_changes"] = [
    "Days and price type — 7 / 30 / 90 days or 1 year; All, DP, MRP, To retailer.",
    "Totals — changes, up / down, stock value effect.",
    "✨ Margin alerts — DP up but retailer price not; the price that keeps your margin, Use ₹… to set it.",
    "Categories — average change per category.",
    "Timeline — each change by day: old price (struck) → new price, % pill, source, season, who.",
]
SHOWS["dist_articles"] = SHOWS["dist_articles"] + [
    "Price changes banner — changes in the last 30 days and margin alerts; tap to open Price changes.",
    "Badge on a card — the latest change, e.g. DP ▲ 10% ₹150 → ₹165.",
    "Article sheet — price history chart (DP, to retailer, MRP) and the list of changes.",
]

# Separate data for every UI (owner, 20-11): the Distributor UI has its own firm — its own items, bills, parties,
# notes and settings — and never shows the Business UI's data (and back).
SHOWS["dist_articles"] = SHOWS["dist_articles"] + [
    "Own firm — only the articles of your distribution firm show here; the Business UI's items never do.",
]

# Delete items (owner 20-11): one from its form, many with Select to delete — both go to the Recycle bin.
SHOWS["products"] = SHOWS["products"] + [
    "Delete — open an item and tap Delete; it goes to the Recycle bin and can be restored.",
    "Select to delete — tick many items (or Select all) and tap Delete N; all go to the Recycle bin.",
]

# Any company sheet (owner 20-11): every column kept, roles remembered per sheet layout, ✨ AI for unclear columns.
SHOWS["dist_articles"] = SHOWS["dist_articles"] + [
    "Columns — after reading an Excel sheet, each column shows what it is used as (name, MRP, DP, PTR, detail, skip); change any and Read again.",
    "Details — every other column (TC, size, shade, ply, bale pack, MOQ…) is kept on the article and can be searched.",
    "✨ AI match columns — AI suggests what unclear columns are; you check before saving.",
    "Remembered — the company's next sheet with the same columns maps itself.",
    "Checks — PTR and DP are checked against the sheet's own margin / mark-up % (with or without GST).",
]

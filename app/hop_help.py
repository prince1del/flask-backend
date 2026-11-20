"""HoP in-app help: a short guide for every screen (phone ⓘ button, web ? button, Ask Nexora).

One list for phone and web. Keys are the screen keys used by both (aliases map the few that
differ). Each topic: title, what (one line), steps (how to use), tips, ai (how AI helps here).
Plain English, short sentences.
"""
from __future__ import annotations

from typing import Any

_DOC_STEPS = [
    "Tap + New (bottom right) to start.",
    "Pick the party, then add items: name, qty, rate, GST. A new row appears by itself.",
    "Tap Save & preview. From the preview you can print, share on WhatsApp or edit.",
]

HELP: dict[str, dict[str, Any]] = {
    "dashboard": {"title": "Dashboard", "what": "Your business at a glance — quotations, orders, money due, cash.",
                  "steps": ["Tap any card to open that list.", "Pull the refresh button to load the latest numbers."],
                  "tips": ["The firm name at the top of the menu shows which firm you are in.",
                           "Every UI has its own data: the Distributor UI has its own firm (items, bills, parties, notes, settings) and never sees the Business UI's data — and back.",
                           "No internet? Lists you opened before still show (yellow “Offline” line with the time saved); new bills wait on the phone and send themselves later."],
                  "ai": "NEXORA Guard (top card) checks your data and lists mistakes to fix."},
    "parties": {"title": "Parties", "what": "Customers and suppliers you sell to or buy from.",
                "steps": ["Tap Add Party, fill the name (and GSTIN / phone if you have them), then Save.",
                          "Pick a Party Group (Hotel, Architect…) — the list comes from Settings.",
                          "Tap a party to edit it or see its bills."],
                "tips": ["Type a GSTIN and tap Fetch to fill the name and address.",
                         "Import many parties at once with Import parties from Excel."],
                "ai": "Import from Excel checks every row (bad GSTIN, wrong phone, duplicates) before saving."},
    "invoices": {"title": "Sale Invoices", "what": "Bills you give to customers. Stock and the customer's balance update by themselves.",
                 "steps": _DOC_STEPS + ["Received now: type what the customer paid with the bill and pick Cash or a bank."],
                 "tips": ["Scan barcode adds an item; scanning it again adds 1 to the quantity.",
                          "Extra charges (freight, packing), TCS / TDS, PO no. and time are lower down the form."],
                 "ai": "✨ Fill with AI: take a photo of any bill, order or handwritten note — AI fills the customer and items. You check and save."},
    "sale_estimates": {"title": "Estimate / Quotation", "what": "Price offers before a sale. Convert one to an invoice when the order comes.",
                       "steps": _DOC_STEPS + ["From the preview tap Convert to Sale Invoice / Order."],
                       "tips": ["Sections (Shortlisted-1, 2…) group items room by room."],
                       "ai": "✨ Fill with AI reads a customer's enquiry or a handwritten list into the quotation."},
    "sale_proforma": {"title": "Proforma Invoice", "what": "An advance bill for payment before delivery. It does not change stock or books.",
                      "steps": _DOC_STEPS, "tips": ["Convert it to a Sale Invoice when goods go."],
                      "ai": "✨ Fill with AI works here too."},
    "payments": {"title": "Payment-In", "what": "Money received from customers.",
                 "steps": ["Tap + New, pick the customer and type the amount.", "Pick Cash, a bank or Cheque.",
                           "The app links the money to the oldest unpaid bills — change it if needed, then Save."],
                 "tips": ["Money not linked to a bill stays as an advance and can be linked later."],
                 "ai": "Bills are matched oldest-first automatically."},
    "sale_orders": {"title": "Sale Order", "what": "Orders taken from customers, before billing.",
                    "steps": _DOC_STEPS + ["Convert to a Sale Invoice or Delivery Challan from the preview."],
                    "tips": [], "ai": "✨ Fill with AI reads a customer's order photo."},
    "sale_challan": {"title": "Delivery Challan", "what": "Goods sent without a bill yet.",
                     "steps": _DOC_STEPS + ["Convert to a Sale Invoice later."], "tips": [], "ai": "✨ Fill with AI works here too."},
    "sale_returns": {"title": "Sale Return / Credit Note", "what": "Goods a customer sent back. Stock comes back, the customer owes less.",
                     "steps": _DOC_STEPS, "tips": ["Settle it against the customer's open bills."], "ai": ""},
    "commission": {"title": "Commission", "what": "Commission you give an agent on a sale bill — %, TDS and what is paid.",
                   "steps": ["Tap + Commission and pick the bill (Due / No commission / All).",
                             "Pick who gets it, type commission % and TDS %, mark Paid with the date when you pay.",
                             "Tick 'Use for every bill of this customer' so their next bills show as Due."],
                   "tips": ["Commission is on the bill value before GST; TDS is on the commission (194H, 2%)."],
                   "ai": "Ask Nexora: \"Raju ki kitni commission baaki hai\"."},
    "purchase_bills": {"title": "Purchase Bills", "what": "Bills you receive from suppliers. Stock goes up, you owe the supplier.",
                       "steps": _DOC_STEPS + ["Paid now: type what you paid with the bill."],
                       "tips": ["Reverse charge and place of supply are lower down the form."],
                       "ai": "✨ Read bill with AI: photo or PDF of the supplier's bill fills vendor, items and GST."},
    "purchase_payment_out": {"title": "Payment-Out", "what": "Money paid to suppliers.",
                             "steps": ["Tap + New, pick the supplier, type the amount, pick Cash / bank / cheque, Save."],
                             "tips": ["The payment is linked to the oldest unpaid bills; change it if needed."], "ai": ""},
    "purchase_expenses": {"title": "Expenses", "what": "Rent, salary, electricity, petrol and other running costs.",
                          "steps": ["Tap + New, pick a category (or a party), type the amount, pick how you paid, Save."],
                          "tips": ["Add your own categories with + New category."],
                          "ai": "✨ Read bill with AI fills the amount and guesses the category from a receipt photo."},
    "other_income": {"title": "Other Income", "what": "Money that is not a sale — rent received, interest, scrap sale.",
                     "steps": ["Tap + New, pick an income category, type the amount and where the money went, Save."],
                     "tips": ["It shows in Profit & Loss as other income."], "ai": ""},
    "purchase_orders": {"title": "Purchase Order", "what": "Orders you place on suppliers.", "steps": _DOC_STEPS,
                        "tips": ["Convert to a Purchase Bill when the bill comes."], "ai": ""},
    "purchase_returns": {"title": "Purchase Return / Debit Note", "what": "Goods you sent back to a supplier.",
                         "steps": _DOC_STEPS, "tips": [], "ai": ""},
    "journal_entries": {"title": "Journal Entry", "what": "Adjustments between accounts that are not a sale or purchase.",
                        "steps": ["Tap + New, pick the party, type the amount and a note, Save."], "tips": [], "ai": ""},
    "products": {"title": "Catalogue (items)", "what": "Everything you sell or buy, with rates, GST and stock.",
                 "steps": ["Tap + Product, fill name, rates, GST %, HSN, unit and stock, Save.",
                           "Choose Service for work like stitching — services never keep stock.",
                           "Tap an item to edit it."],
                 "tips": ["Share online catalogue gives customers a link to order from your items — orders arrive as Sale Orders and you get an email.",
                          "Wholesale price is used by itself when the quantity reaches the wholesale minimum.",
                          "2nd unit: e.g. 1 BOX = 10 PCS; a bill in BOX moves 10 PCS of stock.",
                          "Import or bulk-update items from Excel."],
                 "ai": "The item form warns about mistakes, e.g. sale price above MRP or wholesale below cost."},
    "stock": {"title": "Stock", "what": "Live quantity of every item.",
              "steps": ["See what is in stock; add a stock journal to correct a count."], "tips": [], "ai": ""},
    "app_updates": {"title": "Updates", "what": "What changed in the Business UI on each date, plus app-wide changes (app lock, Ask Nexora).",
                    "steps": ["A new version shows “New” — tap Install now.", "Changes made only for other UIs are not listed here."],
                    "tips": ["The update window pops up only when something changed for you."], "ai": ""},
    "reminders": {"title": "Reminders", "what": "Two tabs. PAYMENTS = customers who owe you money on sale bills. SERVICE = customers whose sold item (AC, RO, generator…) is due for service.",
                  "steps": ["Open Payments, tap WhatsApp / SMS / Email on a card to remind that customer.",
                            "For services: open the item → “Service every (days)” (AC 180, RO 90…). Every sale of that item then shows in the Service tab when due.",
                            "After the service visit tap Done — the next one is counted from that day.",
                            "Settings → Reminders: a 9 am email with today's list (payments + services), automatic payment emails, a daily backup email."],
                  "tips": ["Change the words in Settings → Sharing (payment reminder and service reminder messages).",
                           "A customer is not emailed again before the repeat gap you set.",
                           "A bill without a due date uses bill date + default due days (Settings → Transaction)."],
                  "ai": "Ask Nexora “aaj kisko payment reminder bhejna hai?” or “kiski AC service due hai?”; the item form suggests a service period; Guard flags overdue money."},
    "manufacture": {"title": "Manufacturing", "what": "Make finished items from raw materials — stock and cost follow.",
                    "steps": ["Raw materials of an item: pick the finished item, add each raw material and the qty for ONE piece, Save.",
                              "Manufacture: pick the item and quantity, tap Check stock & cost, then Save.",
                              "Made by mistake? Tap Undo — raw materials go back and the made items come out."],
                    "tips": ["Extra cost (labour, power) is added to the cost per piece.",
                             "The first time an item is made, its cost per piece becomes its purchase price for profit reports."],
                    "ai": "✨ Suggest with AI fills the raw-material list from your own items; Ask Nexora “kitne curtain bana sakte hain?”."},
    "godowns": {"title": "Godowns", "what": "Stock kept in each store or warehouse.",
                "steps": ["Add a godown by name.", "Tap an item to move stock from one godown to another.",
                          "Pick the godown on a bill to sell or buy from it."],
                "tips": ["Main godown holds everything not moved elsewhere."],
                "ai": "A bill warns when the chosen godown does not have enough stock."},
    "banking": {"title": "Cash & Bank", "what": "Cash in hand, bank accounts, cheques and transfers.",
                "steps": ["+ Account adds a bank account.", "New bank entry: deposit, withdraw or transfer.",
                          "Cheques: tap a cheque to deposit it or mark it withdrawn.",
                          "Tap a bank account to reconcile it with the bank statement."],
                "tips": ["Upload the bank statement (CSV) in Reconcile — matching entries are ticked for you."],
                "ai": "Reconcile explains statement lines missing in your books (bank charges, interest…)."},
    "gst_reports": {"title": "GST reports", "what": "GSTR-1, GSTR-3B, GSTR-9 and HSN summaries.",
                    "steps": ["Pick the report and the dates.", "Download the file or read the totals."],
                    "tips": ["E-Invoice and E-Way Bill files are on each sale invoice's preview."],
                    "ai": "✨ AI insights explains the GST you have to pay and what to check."},
    "books": {"title": "Reports", "what": "Profit & Loss, Balance Sheet, party and item reports — 30+ reports.",
              "steps": ["Pick a report, set the dates, read it.", "Tap ✨ AI insights for a short explanation."],
              "tips": ["Party Statement shows one party's full ledger."],
              "ai": "✨ AI insights gives 3–5 plain points and one action for each report."},
    "receivables": {"title": "Receivables", "what": "Who owes you money and for how long.", "steps": ["See money due by age."],
                    "tips": [], "ai": "Ask Nexora: “kis party ka paisa baaki hai?”"},
    "overdue_bills": {"title": "Overdue bills", "what": "Bills past their due date.",
                      "steps": ["Tap a bill to send a payment reminder on WhatsApp."], "tips": [],
                      "ai": "Reminder text can be written by AI."},
    "stock_reorder": {"title": "Reorder stock", "what": "Items running low, based on the last 30 days of sales.",
                      "steps": ["Order what is listed before it runs out."], "tips": [], "ai": ""},
    "app_settings": {"title": "Settings", "what": "How bills work: numbering, GST, print, sharing, party groups.",
                     "steps": ["Switches save at once; text fields save when you tap Save."],
                     "tips": ["Your UPI ID here puts a UPI QR on sale invoices.",
                              "Print: header line, footer, HSN column, HSN-wise tax summary, page margins and the currency symbol.",
                              "Bills show CGST + SGST for your own state and IGST for other states — keep your state in Company profile.",
                              "App lock (PIN or fingerprint) and Backup are at the bottom.",
                              "Government & approvals (owner): switch on E-Invoice (IRN, signed QR on the bill), E-Way Bill, live GSTIN verify, SMS by NEXORA and online payment links — Test first with sandbox keys, Live after approval.",
                              "Backup downloads one locked .kunwar file — it opens only in NEXORA and only for your company. Restore a backup into a new firm never changes your current data.",
                              "Party → Loyalty points: customers earn 1 point per ₹ you choose and use them as a discount on a sale invoice."],
                     "ai": "Ask Nexora “bill pe HSN summary kaise dikhaye?” — it answers from this guide."},
    "staff_permissions": {"title": "Staff permissions", "what": "What each staff login can see or change.",
                          "steps": ["Pick a quick choice (Salesperson, Accountant…) or set each area.",
                                    "Owner always has full access."],
                          "tips": ["New staff start with full access — set them right after they join."],
                          "ai": "A plain summary shows what the person can do; risky choices are warned."},
    "audit_log": {"title": "Audit trail", "what": "Every change on phone or web — who, when, and the old amount.",
                  "steps": ["Search by bill number or name."], "tips": [], "ai": "Ask Nexora: “kisne bill badla?”"},
    "company_profile": {"title": "Company profile", "what": "Your firm's name, GSTIN, address, bank, logo and signature for bills.",
                        "steps": ["Fill the details and Save — they print on every bill."],
                        "tips": ["Your state (or GSTIN) decides CGST + SGST vs IGST on bills — without it bills just say GST."],
                        "ai": "Verify GSTIN fills name, address and state for you."},
    "financial_year": {"title": "Financial year", "what": "Close a year to lock its documents.",
                       "steps": ["Tap a year to close it after your accounts are final."], "tips": [], "ai": ""},
    "tally_export": {"title": "Export to Tally", "what": "Vouchers for Tally and a day-book CSV.",
                     "steps": ["Pick the dates, tap Apply period, download."], "tips": [], "ai": ""},
    "dist_articles": {"title": "Article Master", "what": "Every article: code, MRP, your buying price (DP), your price to retailers, margin, season — with price history.",
                      "steps": ["✨ Read company price list: pick the Excel, PDF or photo — you see new articles and price changes, untick wrong ones, Save.",
                                "Tap an article to edit it and see its price history.", "+ Article adds one by hand."],
                      "tips": ["SO and company invoices are checked against these prices, so keep DP right."],
                      "ai": "AI reads the price list, flags big price jumps, articles without DP, selling above MRP or below cost."},
    "dist_price_changes": {"title": "Price changes", "what": "Every price change in the Article Master: old → new, % up or down, when and from where.",
                           "steps": ["Pick the days (7 / 30 / 90 / 1 year) and the price (DP / MRP / to retailer).",
                                     "Look at Margin alerts first: DP went up but your price to retailers did not.",
                                     "Tap Use ₹… to set the price that keeps your old margin (you confirm)."],
                           "tips": ["Red ▲ on DP = you pay more; green ▲ on your own price = you earn more.",
                                    "Stock value = what the stock you hold costs more (or less) at the new DP."],
                           "ai": "AI finds margin drops, suggests the new retailer price and explains the changes (✨ AI insights)."},
    "dist_retailer_dues": {"title": "Retailer payments & follow-up", "what": "Who owes you, since when, what they promised, and who to call today.",
                           "steps": ["Open Call today first.", "Tap a retailer: call, send a WhatsApp reminder, note what happened and the promised date."],
                           "tips": ["A promise is kept when a payment comes in after it; it is broken when the date passes without one."],
                           "ai": "✨ Call today is ordered by overdue money, age, broken promises and how long since the last follow-up."},
    "dist_staff": {"title": "Staff performance", "what": "What each person did in the dates: bills, sales, collections, orders, follow-ups.",
                   "steps": ["Pick the dates.", "Look at the tips — many deletes or bills with no follow-up."],
                   "tips": ["Counted from the audit trail (who saved each entry)."], "ai": "✨ AI insights compare people and point out gaps."},
    "dist_profit": {"title": "Business profitability", "what": "Sales, cost of goods, gross profit, expenses and net profit — by month, item, retailer, company.",
                    "steps": ["Pick the period.", "Switch between Items, Retailers, Companies and Below cost."],
                    "tips": ["Cost uses the buying price (DP) in the Article Master; items without DP show too much profit."],
                    "ai": "AI flags sales below cost, low-margin items and items without a buying price."},
    "dist_orders": {"title": "Orders → SO → CI", "what": "Your orders to the company, the company's SO and its invoice (CI) — status of each order.",
                    "steps": ["Set company once: tick the brand you distribute for.",
                              "+ New order makes a Purchase Order to the company.",
                              "When the company sends the SO: + SO received — ✨ AI reads the SO PDF / photo, you check and Save.",
                              "When the CI comes: save it as a Purchase Bill (Convert the order), or Link CI to the SO."],
                    "tips": ["Status: Ordered → SO received → Part billed → Billed.", "Tap an order or CI to open its copy."],
                    "ai": "AI reads the SO PDF into the form; ✨ AI insights say which orders are stuck."},
    "dist_ledger": {"title": "Company ledger & payments", "what": "What the company billed, what you paid, what is due and overdue.",
                    "steps": ["Open it to see Billed, Paid, To pay and Overdue.", "Tap a company invoice to see it; pay with Payment-Out."],
                    "tips": ["Overdue uses the due date on each company invoice."],
                    "ai": "✨ AI insights point out overdue invoices and payment patterns."},
    "dist_retailer_goods": {"title": "Goods per retailer", "what": "Which retailer got which goods — from sale invoices and challans.",
                            "steps": ["Pick the dates.", "Search a retailer or an item.", "Tap a retailer to see the items; tap an item to open its last bill."],
                            "tips": ["Challans count as goods sent but not as money."],
                            "ai": "✨ AI insights show who buys more or less than before."},
    "dist_warehouse": {"title": "Warehouse", "what": "Stock now and what it is worth.", "steps": ["Search an item; tap Out of stock to see only those."],
                       "tips": ["Stock moves with company invoices (in) and sale bills (out)."],
                       "ai": "What to order opens the AI forecast."},
    "dist_forecast": {"title": "Forecast & what to order", "what": "From your sale speed: days of stock left, what to order and by when.",
                      "steps": ["Pick how many days of sales to look at and how many days of stock to keep.",
                                "Order now = will run out before a new order arrives.",
                                "✨ Make the suggested order to the company — you check the list, then Make order saves a Purchase Order."],
                      "tips": ["Lead time = the company's delivery days (set on the supplier or the item)."],
                      "ai": "AI suggests the order and explains trends; nothing is saved until you tap Make order."},
    "bulk_bills": {"title": "Print many bills", "what": "Every bill between two dates in one PDF — print, save or share all at once.",
                   "steps": ["Pick the dates (or This month / Last month / This financial year).",
                             "Tick what to include — sale invoices, purchase bills, expenses, notes, payments …",
                             "Switch on Add supplier's bill copies to put each supplier's bill right after its purchase bill.",
                             "Tap Print all, Save PDF or Share."],
                   "tips": ["Up to 400 bills in one PDF — for more, do it month by month.",
                            "GST pack for last month picks what your CA asks for at GST time."],
                   "ai": "Warns when purchase bills in these dates still have no supplier copy."},
    "loans": {"title": "Loan accounts", "what": "Loans taken; EMIs and interest go to the books.", "steps": ["+ Loan, then add entries."],
              "tips": [], "ai": ""},
    "fixed_assets": {"title": "Fixed assets", "what": "Machines, furniture, vehicles — with depreciation.",
                     "steps": ["+ Asset, then run this month's depreciation once a month."], "tips": [], "ai": ""},
    "recycle_bin": {"title": "Recycle bin", "what": "Deleted bills and items — restore them from here.", "steps": ["Tap Restore."],
                    "tips": [], "ai": ""},
    "item_settings": {"title": "Items & units", "what": "Item categories and units used on bills.", "steps": ["Add a category or unit."],
                      "tips": [], "ai": ""},
    "print_theme": {"title": "Print theme", "what": "Colour of your printed bills.", "steps": ["Pick a colour."], "tips": [], "ai": ""},
}
ALIASES = {"sale_invoices": "invoices", "reports": "books", "purchase_fa": "purchase_bills", "sale_fa": "invoices"}

# The other menu screens (samples, dispatch, complaints, targets, import…) live in hop_help_more.
from app.hop_help_more import ALIASES_MORE as _AM, MORE as _MORE  # noqa: E402

for _k, _v in _MORE.items():
    HELP[_k] = {k: v for k, v in _v.items() if k != "shows"}
ALIASES.update(_AM)


def _with_shows(k: str, t: dict[str, Any]) -> dict[str, Any]:
    from app.hop_help_screens import SHOWS

    return {"key": k, **t, "shows": SHOWS.get(k, [])}


def topic(key: str) -> dict[str, Any] | None:
    k = ALIASES.get(key, key)
    t = HELP.get(k)
    return _with_shows(k, t) if t else None


def all_topics() -> dict[str, Any]:
    return {"topics": {k: _with_shows(k, v) for k, v in HELP.items()}, "aliases": ALIASES}


def search(q: str) -> list[dict[str, Any]]:
    """For Ask Nexora: topics whose words match the question (simple, no AI cost)."""
    words = [w for w in q.lower().replace("?", " ").split() if len(w) > 2]
    scored = []
    for k, v in HELP.items():
        from app.hop_help_screens import SHOWS

        text = " ".join([v["title"], v["what"], *SHOWS.get(k, []), *v["steps"], *v["tips"], v["ai"]]).lower()
        s = sum(1 for w in words if w in text) + (3 if any(w in v["title"].lower() for w in words) else 0)
        if s:
            scored.append((s, _with_shows(k, v)))
    return [t for _, t in sorted(scored, key=lambda x: -x[0])[:3]]

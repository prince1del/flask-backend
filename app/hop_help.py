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
                  "tips": ["The firm name at the top of the menu shows which firm you are in."],
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
    "commission": {"title": "Commission", "what": "Commission bills raised to suppliers.", "steps": _DOC_STEPS, "tips": [], "ai": ""},
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
                 "tips": ["Wholesale price is used by itself when the quantity reaches the wholesale minimum.",
                          "2nd unit: e.g. 1 BOX = 10 PCS; a bill in BOX moves 10 PCS of stock.",
                          "Import or bulk-update items from Excel."],
                 "ai": "The item form warns about mistakes, e.g. sale price above MRP or wholesale below cost."},
    "stock": {"title": "Stock", "what": "Live quantity of every item.",
              "steps": ["See what is in stock; add a stock journal to correct a count."], "tips": [], "ai": ""},
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
                              "App lock (PIN) and Backup are at the bottom."],
                     "ai": ""},
    "staff_permissions": {"title": "Staff permissions", "what": "What each staff login can see or change.",
                          "steps": ["Pick a quick choice (Salesperson, Accountant…) or set each area.",
                                    "Owner always has full access."],
                          "tips": ["New staff start with full access — set them right after they join."],
                          "ai": "A plain summary shows what the person can do; risky choices are warned."},
    "audit_log": {"title": "Audit trail", "what": "Every change on phone or web — who, when, and the old amount.",
                  "steps": ["Search by bill number or name."], "tips": [], "ai": "Ask Nexora: “kisne bill badla?”"},
    "company_profile": {"title": "Company profile", "what": "Your firm's name, GSTIN, address, bank, logo and signature for bills.",
                        "steps": ["Fill the details and Save — they print on every bill."], "tips": [], "ai": ""},
    "financial_year": {"title": "Financial year", "what": "Close a year to lock its documents.",
                       "steps": ["Tap a year to close it after your accounts are final."], "tips": [], "ai": ""},
    "tally_export": {"title": "Export to Tally", "what": "Vouchers for Tally and a day-book CSV.",
                     "steps": ["Pick the dates, tap Apply period, download."], "tips": [], "ai": ""},
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


def topic(key: str) -> dict[str, Any] | None:
    k = ALIASES.get(key, key)
    t = HELP.get(k)
    return {"key": k, **t} if t else None


def all_topics() -> dict[str, Any]:
    return {"topics": {k: {"key": k, **v} for k, v in HELP.items()}, "aliases": ALIASES}


def search(q: str) -> list[dict[str, Any]]:
    """For Ask Nexora: topics whose words match the question (simple, no AI cost)."""
    words = [w for w in q.lower().replace("?", " ").split() if len(w) > 2]
    scored = []
    for k, v in HELP.items():
        text = " ".join([v["title"], v["what"], *v["steps"], *v["tips"], v["ai"]]).lower()
        s = sum(1 for w in words if w in text) + (3 if any(w in v["title"].lower() for w in words) else 0)
        if s:
            scored.append((s, {"key": k, **v}))
    return [t for _, t in sorted(scored, key=lambda x: -x[0])[:3]]

# HoP ⇄ Vyapar parity — progress note

Goal (owner's words): House of Prizm (HoP) in NEXORA should **work like the
Vyapar software** — same screens, flows and wiring — not just hold Vyapar data.
Own NEXORA / HoP branding (no Vyapar logo). AI help added in every phase.
Order agreed: Phase 1 → 2 → 3 → 4, each deployed and checked before the next.

_Last updated: 2026-11-15 (deploy Phase 1+2 live; Phase 3 started)._

## Status at a glance
| Phase | What | State |
|---|---|---|
| 1 | Money flow: paid/received, Cash & Bank, cheques, Payment-In/Out against bills, stock from documents, expense categories, Convert, AI bill reader, Ask Nexora `get_hop_books` | **Deployed** on Lightsail `3.7.170.77` at `f76a9b7` (backup `/var/data/backup_before_phase12_20261115.sqlite3`). Smoke 401 on money routes. |
| 2 | Accounts & reports (real double-entry: TB, P&L, Balance Sheet, Cash Flow; Day Book, All Transactions, Party Statement, outstanding; sale/purchase/item/stock/bill-wise profit/expense reports; GSTR-1/2/3B, HSN) + Reports screen + AI insights | **Deployed** with Phase 1 (same commit). Smoke 401 on `/books`, `/books/trial_balance`, insights. |
| 3 | Items & settings + print themes, recycle, loans, Fixed Assets, FY close, Tally export | **Built (web+server).** First slice + rest in `hop_settings.py` / `hop_phase3_ext.py` + Settings screens. Deploy after tests. |
| 4 | Phone screens every phase | **In progress** — money / books / settings on Android |

## Phase 3 rest (2026-11-15)
- Print themes (`/print-theme` + PDF accent)
- Recycle bin soft-delete docs/products
- Loan accounts on Balance Sheet
- Fixed assets + monthly depreciation
- Financial year close + write guard
- Tally XML/CSV export


## Phase 1 — done (details in docs/NEXORA_APP_MAP.md §4 "Money flow")
- Server: `app/hop_money.py` (post/undo effects per document, balances recomputed
  from entries), routes in `app/routes/hop.py` (`/open-bills`, `/cheques…`,
  `/expense-categories`, `/party-transactions/<id>/convert|links`, `/ai/read-bill`),
  schema additions in `app/hop_schema.py` (new columns + `hop_payment_links`,
  `hop_cheques`, `hop_expense_categories`).
- AI: `app/services/hop_ai.py` (bill photo/PDF → form; Ask Nexora tool data).
- Web: `app/static/hop_money_web.js` + hooks in `hop_commercial_web.js`, `hop_app.js`.
- Tests: `tests/test_hop_money_flow.py` (12). All HoP + Ask Nexora tests pass (103).
- Checked in a browser on a made-up company: invoice → payment → convert → expense → cheque.
- Not yet checked: live AI bill read (no Gemini key on the laptop — try once on prod,
  costs < ₹1). Documents made before Phase 1 get stock/cash effects only when re-saved.

## Phase 2 — plan (in progress)
1. `app/hop_reports.py`: build a double-entry ledger on the fly from documents +
   bank entries + cheques + opening balances (no stored journal, so it is always
   right after edits). Accounts: each Cash/Bank, Cheques in hand, Receivable/Payable
   per party, Sales, Sales Returns, Purchases, Purchase Returns, Expense per category,
   Output GST, Input GST, Capital (deposits), Drawings (withdrawals), Opening Balance
   Equity, Opening Stock. Imported Vyapar docs use their totals; their payments go
   to "Cash & Bank (Vyapar import)".
2. Reports from the ledger: Trial Balance, P&L (with opening/closing stock at
   purchase price), Balance Sheet (must balance), Cash Flow.
3. Lists: Day Book, All Transactions, Party Statement (running balance), party-wise
   outstanding, Sale / Purchase report, item-wise sale/purchase, Stock summary,
   Low stock, Stock movement, Bill-wise profit, Expense report.
4. GST: GSTR-1 (B2B/B2C), GSTR-2 (purchases), GSTR-3B summary, HSN summary.
5. Web "Reports" screen: list on the left, filters (dates, party), table, totals,
   Excel/CSV + Print.
6. AI: "Explain this report" insights (one capped Gemini call) and Ask Nexora
   (profit, party statement, day book).
7. Replace Cursor's old TB/P&L/BS in `app/hop_books.py` (plug figure + profit
   counted twice) with the new ones.
- Testing: unit tests on made-up data (public repo — never real data in tests);
  owner allowed checking against a LOCAL copy of real HoP data (scratchpad only,
  delete after; never commit it).

## Screens checklist (owner's rule: no feature without its buttons — web AND phone)
| Feature | Web (desktop) | Phone |
|---|---|---|
| Received/Paid + balance on invoice/bill | ✅ | ⏳ |
| Payment-In/Out with bill list + auto link | ✅ | ⏳ |
| Expense with category, AI bill read | ✅ | ⏳ |
| Cash & Bank (deposit/withdraw/transfer/statement) + Cheques | ✅ | ⏳ |
| Convert to … buttons | ✅ | ⏳ |
| Reports (21) + filters + Excel/Print + AI insights | ✅ | ⏳ |

## No "Vyapar" name on screen (owner's rule, 2026-11-15)
Menu is "Import old data"; imported things are called "Imported"; messages say "backup".
Code names (files, CSS `hop-vyp-*`, functions) stay — users never see them.
Still showing the old name: data already on prod saved by the old importer (customer
"source", vendor remarks, document notes "Imported from Vyapar txn …"). Needs a one-time
text update on prod — **ask the owner first** (no bulk data changes without OK).
Phone app strings: change during the phone work.

## AI checklist (owner's rule: every feature ships with AI help — never "later")
Before a phase is called done, every row must be ✅ or have a written reason.
| Area | AI help | State |
|---|---|---|
| Purchase Bill / Expense entry | ✨ Read bill photo/PDF → form (vendor, items, GST, category, mismatch warnings) | ✅ Phase 1 (live test pending on prod) |
| Payment-In / Out | Smart match: oldest bills first | ✅ Phase 1 (rule-based, no AI cost) |
| Cash, dues, expenses | Ask Nexora `get_hop_books` (cash_bank, receivables, payables, unpaid_bills, expenses) | ✅ Phase 1 |
| Reports (P&L, BS, cash flow, outstanding, expenses, bill profit, sales, GSTR-3B) | ✨ AI insights button (3–5 points + one action) | ✅ Phase 2 (live test on prod pending) |
| Reports via chat | Ask Nexora: profit & loss, balance sheet, party statement, day book, GST | ✅ Phase 2 |
| Sale invoice entry | Suggest item rate from party's last price; warn on unusual price / duplicate bill no. | ⏳ Phase 3 (with party-wise rates) |
| Party payments | Due-date reminders + AI-written WhatsApp payment reminder text | ⏳ Phase 3 |
| GST | Flag missing GSTIN / HSN / wrong rate before filing | ⏳ Phase 2 follow-up |
| Stock | Low-stock reorder suggestion from sales speed | ⏳ Phase 3 |
| Phone | Same AI buttons on the phone screens | ⏳ Phase 4 |
| Bill extras (charges, free qty, TCS) | ✨ Bill reader fills charges, free qty, TCS; total-mismatch check counts them | ✅ 2026-11-16 |
| Items (service, MRP, wholesale, 2nd unit, barcode) | Ask Nexora `items` (stock, barcode lookup, item problems); rule checks on the item form | ✅ 2026-11-16 |
| Other income | Ask Nexora `other_income`; P&L insights include it | ✅ 2026-11-16 |
| Bank reconciliation | ✨ Statement auto-match + plain hints for lines missing in books; Ask Nexora cash_bank shows unticked totals | ✅ 2026-11-16 (rule based, no AI cost) |
| Sharing (WhatsApp, UPI link) | Existing ✨ AI reminder text (`/ai/payment-reminder`) + owner's templates | ✅ 2026-11-16 |
| Rest of NEXORA (BD side) | NEXORA Guard plan (rules + AI explain, issues inbox, daily digest) — agreed earlier, after tracking | ⏳ after HoP phases |

## How to deploy (for whoever continues)
1. Full tests: `python -m pytest tests -q` (≈22 min, must be all green).
2. Backup on server: sqlite `.backup` of `/var/data/centralized_db.sqlite3` to
   `/var/data/backup_before_<what>_<date>.sqlite3` (see this session's pattern).
3. `ssh -i ~/.ssh/nexora_server ubuntu@3.7.170.77 'cd /opt/nexora && sudo -u nexora git pull -q --ff-only && sudo systemctl restart nexora'`
4. Smoke test new endpoints (401 = exists), check `journalctl -u nexora` for errors.
5. Update this file + docs/NEXORA_APP_MAP.md.


## Review of Cursor's Phase 3 + phone work (2026-11-15 night, by Claude)
Live on prod: `7191088` (Phase 1+2+3; backups taken before each deploy). `2eb1d1d` (GST
warnings) is pushed but NOT deployed. Phone OTA `2.0.458953` published by Cursor.
Bugs found (proved on a throwaway test DB) — **to fix next**:
1. Deleted (recycle bin) documents still count: open bills for Payment-In, Ask Nexora
   receivables, lists (`hop_money.open_bills`, `hop_ai.books_answer`, party-transactions
   list route have no `deleted_at` filter).
2. Restore from recycle bin loses "received now" + payment account
   (`hop_phase3_ext.restore_document` calls `post_document(..., {})` as a new doc —
   should re-post with the stored values, `is_update=True`).
3. Loans make the Balance Sheet unbalanced (liability added outside the ledger; the
   money side is posted as Capital/Drawings).
4. Fixed assets make the Balance Sheet unbalanced (cost/depreciation added outside the
   ledger, depreciation not in P&L profit).
5. Phone: "Bank" payment mode sends no `payment_account_id` → server refuses the save.
Smaller: phone "Received now" only on Sale Invoice / Purchase Bill (not returns) and is
not pre-filled when editing; phone cheque tap deposits into the first bank with no choice
or confirm, no Reopen; phone menu still says "Banking".

### Fixed (2026-11-16, commit 3a1bd2f + phone tree)
- 1–4 fixed on the server (tests `tests/test_hop_review_fixes.py`): recycle-bin documents
  count nowhere; restore keeps "received now"; loans + fixed assets are posted inside the
  ledger (BS/TB always balance; interest & depreciation in P&L).
- 5 fixed on the phone: "Payment type" now lists the real Cash / Bank accounts + Cheque and
  sends `payment_account_id`. Also: menu "Cash & Bank"; Cursor's rate-suggestion code
  did not compile (used `updateLines` before it was declared) — fixed.
- Still to do on the phone: "Received now" on returns + pre-fill on edit; cheque deposit
  should ask which account and allow Reopen.
- Deploy: full tests → backup → pull/restart → build + publish phone OTA.

## Overnight plan (owner asleep, 2026-11-16) — owner said: don't stop; NEXORA Guard next
1. ✅ Phone: "Received now / Paid now" on Sale & Purchase Returns; edit shows the amount already
   received/paid and its account. All 90 phone HoP API paths exist on the server.
2. ⏳ Full tests → backup → deploy 3a1bd2f (+ Cursor's GST warnings) → publish phone OTA.
3. ⏳ Live AI bill read once on prod (< ₹1).
4. ⏳ **NEXORA Guard** (AI that catches mistakes early — agreed earlier, "tracking first, then
   Guard"): rules engine + issues inbox (open / resolved / dismissed) + "✨ Explain & fix" (one
   capped AI call) + daily AI digest + Ask Nexora tool + web panel + phone card. Start with HoP
   checks (overdue bills, balance mismatches, negative cash/stock, duplicate bill numbers, missing
   GSTIN/HSN, old open cheques, unconverted estimates, unused advances) and BD checks.
Not without the owner: editing prod data (old-name cleanup), deleting anything, AI tests > ₹10.

### NEXORA Guard — built (2026-11-16 night)
Server + web + phone done and tested (web checked in a browser on made-up data: found
overdue bill, negative cash, duplicate bill, negative stock). Phone card on My Day + HoP
home compiles. See docs/NEXORA_APP_MAP.md §4b. Deploy with the next release.
Owner reminders tonight: **mobile is the first priority**; app must be **full of AI**.

## Released 2026-11-16 (overnight)
- Full tests: 952 passed, 0 failed (commit 18a4a1a). (An earlier run showed 75 ImportErrors — caused by
  editing `app/routes/__init__.py` mid-run; not real. Lesson: never edit server files while tests run.)
- Backup `/var/data/backup_before_guard_fixes_20261115_2005.sqlite3` → deployed `18a4a1a` → service active,
  no errors. Books balance on prod. HoP section on prod has no data yet (Guard finds nothing there).
- Phone OTA **2.0.459075** published (Guard card, bank account on payments, cheque deposit/reopen,
  Received/Paid now on returns, edit pre-fill). Phone wasn't reachable for direct install — it will offer
  the update by itself.
- Live AI bill read tested once on prod with a made-up bill: vendor, GSTIN, bill no, date, both items
  (HSN/qty/unit/rate/GST) and total all correct; warnings correct.

## Next (owner: mobile first, app full of AI)
1. Check the new phone update on the owner's phone (Guard card, bank payment, cheque deposit).
2. Guard: add a daily server run + push notification on the phone for new urgent issues.
3. More AI on the phone: "Read bill" on Purchase Bill already there — add AI to Payment-In
   (suggest which bills from the party's history) and an AI summary on the HoP home.
4. Waiting on owner: OK to clean the old software name from saved prod data.

## Night 2 (2026-11-16) — owner asleep, said "jitna kheech sakte ho kheecho", no permission needed
Gap list vs full desktop billing software was given (GST compliance, billing details, items/stock,
parties, missing reports, roles/audit, multi-firm). Built tonight:
1. **11 more reports** (`app/hop_reports_more.py`): Party-wise P&L, Item-wise P&L, Party report by item,
   Sale/Purchase by party group, Item category, Discount, Expense items, Sale/Purchase order report,
   GSTR-9. Web Reports lists them (+ generic table view for any report).
2. **Phone Reports screen rebuilt** (`ui/hop/HopReportsScreen.kt`): Cursor's showed only "rows: 12
   rows" — now real cards, totals, date chips, party/item picker, tap a bill → preview, ✨ AI insights.
   Menu "Reports (all)" and "GST reports" open it. All 32 server reports work on the phone.
3. **Credit limit warning** before saving a sale invoice (phone + web via entry-warnings).
4. **E-Invoice & E-Way Bill files** (`app/hop_einvoice.py`): government JSON (INV-01 v1.1 / bulk
   e-way format) from sale invoices, problems listed first (GSTIN, PIN, state, HSN, number length),
   then save IRN / Ack / E-Way no back. Phone + web buttons on the sale invoice preview.
   No GSP account needed (upload on portal). Direct API needs the owner's GSP signup later.
Release: full tests → backup → deploy → phone OTA 2.0.459281 (see below for result).
Still missing (next): place of supply + reverse charge + TCS/TDS on bills; additional charges;
price lists / MRP; partial order conversion; UPI QR on PDF; WhatsApp share; thermal print; barcode;
batch/expiry/serial; godowns; Excel import/bulk update; party groups list on server; SMS reminders;
user roles + audit trail; other income; bank reconciliation; multiple firms; backup/restore.

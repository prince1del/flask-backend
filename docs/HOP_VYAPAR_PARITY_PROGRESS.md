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
| 3 | Items & settings: item categories, units, party last-rate + invoice warnings, credit days → due dates + overdue + AI WhatsApp reminder, low-stock reorder. Still pending: print themes, Fixed Assets, recycle bin, close FY, Tally export, loan accounts | **In progress (first slice).** Server `app/hop_settings.py`; routes `/item-categories`, `/units`, `/suggest-rate`, `/entry-warnings`, `/overdue-bills`, `/ai/payment-reminder`, `/stock/reorder`; web `hop_settings_web.js` (Settings → Items & units / Overdue / Reorder). |
| 4 | Phone screens every phase (owner rule). Owner asked deploy → Phase 3 first (2026-11-15); phone Phase 1–3 follows. | Phone Phase 1+2+3: next after Phase 3 web slice ships |

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
| Rest of NEXORA (BD side) | NEXORA Guard plan (rules + AI explain, issues inbox, daily digest) — agreed earlier, after tracking | ⏳ after HoP phases |

## How to deploy (for whoever continues)
1. Full tests: `python -m pytest tests -q` (≈22 min, must be all green).
2. Backup on server: sqlite `.backup` of `/var/data/centralized_db.sqlite3` to
   `/var/data/backup_before_<what>_<date>.sqlite3` (see this session's pattern).
3. `ssh -i ~/.ssh/nexora_server ubuntu@3.7.170.77 'cd /opt/nexora && sudo -u nexora git pull -q --ff-only && sudo systemctl restart nexora'`
4. Smoke test new endpoints (401 = exists), check `journalctl -u nexora` for errors.
5. Update this file + docs/NEXORA_APP_MAP.md.

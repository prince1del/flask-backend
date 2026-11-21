# NEXORA — full feature detail (moved out of CLAUDE.md on 20-11-2026 to keep CLAUDE.md short)

Read this first in every session. Deeper maps: `docs/NEXORA_APP_MAP.md` (every module → route → table → screen),
`docs/HOP_VYAPAR_PARITY_PROGRESS.md` (business batches + AI checklist), `docs/QA_NIGHT_2026-11-18.md` (test log),
`docs/PARTNERS_AND_ROLES_PLAN.md` (partners / roles plan), `AGENTS.md` (single-backend + AI rules),
`deploy/README.md` (server, backups, deploy). This repo is **public**: never put server addresses, keys, passwords,
PINs or real business data in it.

_Last updated: 2026-11-20 (night — Cursor GSTIN + deploy)._

---

## 0. NEXORA DNA (why it exists, how it must feel)

- **What it is:** one app, one login, for the whole Indian trade chain: the company's field team (Executive), the
  distributor, the business owner (billing + accounts, Vyapar-style), the retailer, and the admin who runs the company.
  Each person sees **their own UI**. All UIs sit on **one backend and one set of books**.
- **Promise to the user:** "your whole business in one app — bills, GST, stock, money and your team, with AI that does
  the typing".
- **Feel:** mobile first, premium (navy + gold brand look, elegant themes), calm screens, Hinglish-friendly. Every
  card opens something. Every screen has an Info (ⓘ) guide.
- **AI DNA:**
  - AI is inside every feature: it reads documents into forms, checks entries before they are saved, warns early,
    explains reports, and answers in Ask Nexora.
  - **AI never saves anything by itself.** The user checks and confirms.
  - AI must stay cheap: one capped call, inside the daily limit, every call logged.
- **Data DNA:**
  - One backend: phone, web and desktop read the same `/api/v1` routes.
  - No phone-only business data.
  - Every row carries its `workspace_id`.
  - **Separate data for every UI** (owner, 20-11: "not a single byte of one UI in another"). Every phone call sends
    `X-Nexora-UI` (executive / business / distributor) + `X-Hop-Firm`; the server picks that UI's own firm
    (`hop_firms.active_ws`) and refuses a firm of another UI. See §7 "Own firm".
  - The owner changes data only through the app, never through scripts on the live DB.
- **Brand DNA:** every UI shows the **subscriber's own brand**. Never "House of Prizm" / "PRIZM" for another business.
- **Quality DNA:**
  - A screen gets a T&P (Tested & Passed) mark only after every button on it was pressed on the latest phone build.
  - Tests use made-up companies.
  - Commit + push after every fix.

## 1. Owner's rules (always)

- **One backend for everything.** New feature = server route → phone **and** web.
- **Mobile first.**
  - Build and check on the phone first.
  - The phone is on USB / Wi-Fi debug: install with `adb install`. No OTA while the owner is the only customer.
- **AI in every feature** (never "later"). Fill the AI checklist row before calling a feature done. Ask before any
  test that costs more than ₹10.
- **UI text in English** (chat can be Hinglish).
- **Never run bulk data scripts on the live DB.** Fix the code; the user changes data through the app. Live-DB writes
  from a script are blocked on purpose.
- **Commit + push to `main` after every fix** (this repo). The phone repo has local commits only (no remote yet).
- **T&P marks** (`app/qa_marks.json`, badge on the phone + Settings → T&P list): only after a full every-button pass.
  All marks were cleared on 19-11 for a re-audit.
- **Notes inbox:**
  - The owner writes tasks in Business UI → Notes (Test firm), tagged `claude`. **Work ONLY on notes tagged
    `claude`** (the row shows "👤 claude"); untagged notes are the owner's own.
  - When the phone is connected: read them, do the work, reply in that note (what was done / a plan), then
    **mark it done** — through the app only.
- **Don't touch:**
  - Wipe all data.
  - Import old data on real data.
  - Start day (location).
  - Financial year close.
  - Android permission prompts (the owner taps those).
  - The phone's own lock password.

## 2. Architecture

```
Phone (Android, Kotlin/Compose) ─┐
Web workspace (browser)          ├──► Flask API /api/v1 ──► SQLite workspace DB (business data, per workspace_id)
Desktop (pywebview over the web) ┘     (one server)    ├──► PostgreSQL (older platform tables)
                                                        ├──► Google Drive (document copies, Excel backups)
                                                        └──► Gmail read-only (CI mails, SO payment mails)
```

| Part | Where |
|---|---|
| Server | Flask: `app/web_app.py`, routes `app/routes/*.py`, logic `app/*.py` + `app/services/*.py`, DB `centralized_db_system/db.py` |
| Business / Distributor books ("HoP" in code) | `app/hop_*.py`, routes `app/routes/hop.py` (`/api/v1/hop/*`) |
| Web | `app/templates/index.html`, `app/static/app.js` (Executive), `app/static/hop_app.js` (Business) |
| Desktop | `desktop_app.py` |
| Phone | `AndroidStudioProjects/Nexora20`, package `com.princeoberoi.nexora20`, branch `feature/so-billed-pending` |
| Watch app | Wear OS module `:wear` |
| Production | AWS Lightsail Mumbai, systemd `nexora`, timers for mail scan + backups (`deploy/`) |

- **Auth:** JWT (`app/jwt_service.py`).
- **Firms:** one login can own several firms (`X-Hop-Firm` header, firm switcher). Firms belong to one UI
  (`hop_firms.ui` = business / distributor); the Business firm switcher never lists a Distributor firm, and back.
- **Roles:** owner / staff with per-area view / edit / none (`app/hop_permissions.py`).

## 3. The Hub — one login, five UIs

After login the founder sees **Nexora Hub**: "One login, five experiences".
- **Admin Zone:** team, company profile, workspace control.
- **Executive UI:** Bombay Dyeing team.
- **Business UI:** billing + accounts.
- **Distributor UI:** the distributor's own firm.
- **Retailer UI:** preview only.

A subscriber sees only the UI they were given. Every UI has a 🧭 Nexora Hub button.

**Sign-up / welcome:**
- The first step is the **brand first look**: navy + gold, NX logo glow, "NEXORA · BUSINESS · BILLING · AI",
  3 strengths, glass cards (Service / Business / Distributor / Retailer / Join my company).
- Then the details step:
  - company name, full name, phone, email + 6-digit email code, username, password, secret question;
  - the code box + Send code show only once the email is complete (`name@domain.tld`; Gmail / Yahoo / Outlook / Rediff… need their full domain, so `gmail.co` waits; phone, web and server
    `signup_email_code.is_complete_email` use the same rule);
  - or join a company with an invite code.
- **Login:** username/password, remembered usernames, Forgot user ID / password (email code or secret question).
- **App lock:** PIN + fingerprint / face.

## 4. Admin Zone

- **Home:** numbers, trials ending, server health.
- **Team & access:** UI per person, designation, switch off, new password, sign out a phone, sign-in approvals.
- **Plan & team:** tabs for plan, team, invite.

## 5. Executive UI (Bombay Dyeing distribution team)

**Bottom bar:** My Day · Order Desk · Market Visit · To-Do.
**Drawer:** Ask Nexora, Target vs Achievement, Customers, Distributor Zone, Article Master, PJP, Call List, Settings,
Sign out. The screens and their tabs:

- **My Day:**
  - greeting, Start day / attendance (Team duty card), NEXORA Guard;
  - Target vs Achievement card, season order value (Bed / Bath / TOB), Distributors Payment Status card;
  - global search (articles, distributors, retailers, SO / CI, stock).
- **Order Desk** (core workflow, season bar All / AW26 / …):
  - Filled Order upload → SO pack (analyze, match to order) → CI upload + link → **Pending SO**.
  - Order Desk tree: season → category → distributor.
  - Distributor **Hisaab**: order vs received vs billed, credit notes, check with the party ledger.
  - SO payment mails.
- **Distributor Zone:**
  - Payment Status (deposits, CD %, TDS, due dates, SO-wise collection);
  - Hisaab;
  - Statement of Account (from the SAP GL);
  - Secondary Sale;
  - Grievances.
- **Customers:** distributors + retailers master, Party 360, visiting-card scanner (AI), duplicate finder.
- **Article Master:**
  - 189+ articles, MRP / PTR / Ex-Mill, **season-wise prices** (AW26 / SS27 …), price history;
  - Excel upload with older-season handling, manual add, duplicates & merge, brand aliases.
- **Market Visit:** DSR (retailer visit form: stock, placement, competitor brands, issues), day close, Excel import /
  export.
- **PJP:** monthly journey plan, Excel import.
- **Call List**, **To-Do** (personal + grievance-linked), **Catalogues** (Google Drive PDF / PPT).
- **Mail sync:** Gmail read-only. It brings in CI mails and SO payment mails.
- **Settings:**
  - Profile, Google Drive, Mail sync, Language (7 Indian languages), Customization, Updates;
  - Ask Nexora voice;
  - Smart Watch (Galaxy Watch connect / update);
  - Plans & billing, Manage users, Share NEXORA.
- **Ask Nexora (executive):** visits, PJP, MRP ranges, party alerts, pending SO, hisaab, grievances, contacts. It can
  call / SMS / WhatsApp after you confirm. Has voice and learns question shapes.

The full Executive UI screen check was done on 19-11: every screen opens, no errors.

## 6. Business UI (Vyapar parity + extras) — menu, pages, tabs

**Header:** the business's own name, "Business · NEXORA", user name, firm switcher, offline card.
**Bottom bar:** Home · Orders · Invoices · Parties.
**Top:** ⓘ Info guide, T&P badge, 🧭 Hub. The 🤖 Ask Nexora robot floats on every screen and hides while you type.

**Menu:** Dashboard · Parties · Notes, then 8 folding sections:

1. **Sale & Billing:**
   - Sale Invoices, Estimate / Quotation (+ commercial quotation with sections), Proforma Invoice, Sale Order,
     Delivery Challan, Payment-In, Sale Return / Credit Note, Sale FA.
   - Commission (sits in Sales team).
2. **Purchase & Expense:** Purchase Bills, Payment-Out, Expenses, Other Income, Purchase Order, Purchase Return /
   Dr. Note, Purchase FA.
3. **Items & Stock:**
   - Catalogue: MRP, wholesale, 2nd unit, barcode, service period, product photo. **Delete** in the item form, and
     **Select to delete** (tick many / Select all → Delete N, `POST /hop/products/bulk-delete`) — both go to the
     Recycle bin (phone; web bulk not yet).
   - Stock, Godowns (move stock), Manufacturing (raw-material list, make, undo), Reorder stock, Items & units.
4. **Cash, Bank & Accounts:**
   - Cash & Bank: accounts, entries, cheques, reconciliation.
   - Loan accounts, Fixed assets (depreciation), Journal Entry, Financial year.
5. **Reports & GST:**
   - Reports (all): 32 reports — P&L, Balance sheet, Trial balance, Cash flow, Day book, Party statement,
     outstanding, item / party P&L, bill-wise profit…
   - GST reports: GSTR-1 / 2 / 3B / 9, HSN.
   - Receivables: age buckets, tap a customer → their bills → bill copy.
   - Overdue bills, Profitability, Daily Activity, Customer Dashboard.
   - Targets: yearly + monthly, year % vs month %.
   - **Print many bills**: every bill between two dates in one PDF, with or without supplier copies, GST pack.
   - Export to Tally (XML / CSV).
6. **Sales team & Service:**
   - Commission: bill, agent, %, TDS 194H, net, paid; the customer's usual agent marks bills as Due.
   - Reminders: payment + service, SMS / WhatsApp, AI text.
   - Samples: party + several items, status.
   - Dispatch: bill / challan no., e-way, courier, In transit / Delivered.
   - Complaints, Fabric Preview, Vendor Compare, Orders / PO.
7. **Staff & Attendance:** **Salary** (20-11), Team duty (attendance, live map), Staff permissions (per area
   view / edit / none, owner-only areas), Audit trail.
   - **Salary** (`app/hop_salary.py`, `/api/v1/hop/salary/*`, phone `HopSalaryScreen`; also Distributor → Staff):
     staff (monthly / daily pay, optional NEXORA login → present days from Team duty), Month sheet (days, earned,
     bonus, deductions, advance recovered, net), **Pay** = an Expense "Salary" from cash / bank, **Advance** = an
     Expense "Salary advance" recovered later — so salary is in Cash & Bank, Day book, P&L, cash flow, expense
     reports, distributor profitability, Guard. Undo → Recycle bin. ✨ checks (jump vs last month, advance > a
     month's pay, paid twice), ✨ AI insights, Ask Nexora `salary`.
     **PF / ESI / PT / TDS** (`app/hop_payroll_tax.py`): PF 12% + 12% (+1% EDLI/admin) on basic up to ₹15,000 (or full
     basic), ESI 0.75% + 3.25% up to ₹21,000 gross, professional tax a month, TDS new regime (₹75,000 std deduction,
     nil up to ₹12 lakh, slabs, 4% cess). Government rates live ONLY on the server (`DEFAULTS`, `RATES_AS_OF`) — firms
     save just their switches (`FIRM_KEYS`), so one server change reaches everyone. Pay shows a live slip
     (`/salary/preview`); tab "PF / ESI / TDS" lists the month's dues with due dates (PF/ESI 15th, TDS 7th) → Pay
     saves an Expense (PF / ESI / TDS on salary / Professional tax). Paid payroll APIs exist (e.g. INDPayroll), not
     used; next idea: ✨ monthly AI rate watch that only alerts (owner confirms).
     Look (20-11): navy-gold header (month arrows, salary cost incl. your PF/ESI share, chips Paid x of y / dues /
     advances), PF / ESI setup folded into one row, dues as icon cards with a Paid / Due / late badge, empty states. Checked on
     the phone 20-11 (Month + PF / ESI / TDS tabs, server live). Not yet tried with a real staff member / payment. **Private** permission area `salary` (staff:
     none unless the owner allows; Ask Nexora checks it too); the expense note carries no staff name.
8. **Settings:** Company profile, Settings (bills, GST, print), Print theme (12 colours), Theme (premium palettes),
   Recycle bin, Updates, Import old data, Wipe all data.

The rest of the menu: Plan & team, Nexora Hub, Sign out.

**Home:**
- navy-gold greeting card (gold date, 20-11), NEXORA Guard (AI finds mistakes, ✨ explain & fix);
- one heading "Today at a glance" + refresh; tiles showing 0 / ₹0 are greyed so real numbers stand out;
- overview cards, each opening its screen: quotations pending / sent, orders won, production, dispatch due,
  outstanding, collections today, month sales vs target, gross profit %, cash available, **EBITDA** (+ breakdown);
- Today card, monthly target.

**Lists:**
- Every bill list has search, status chips, held bills bar (⏸ Resume / Discard), and "Copy pending" on purchase lists.
- Calm rows (20-11, `HopInvoicesTab.kt`): party, then "bill no · date"; right side amount (no ".00") + Unpaid /
  "₹… due" / Paid ✓; header "N bills · ₹… to collect".
- Tap a row → the bill copy.

**Bill form:** (calm layout 20-11: party, number / date, items and Received now stay open; "Order & transport",
"Discount, shipping & round off", "Charges, TCS / TDS, loyalty", "Place of supply", "Terms & notes" are folded rows
that show what is filled — `FormFold` in `HopDocumentCreateScreen.kt`; total + Save & preview in one bottom bar)
- Parties and items:
  - customer picker + quick add;
  - catalogue item picker (rate / GST / HSN / unit), unit drop-down;
  - **own barcode scanner** (CameraX + ML Kit).
- Order and transport details: calendar on every date, customer PO no. / date, e-way bill, vehicle, transporter.
- References: supplier bill no. / date, UTR / cheque no., credit / debit note against an invoice (its items fill in).
- Amounts and tax: charges (freight / packing / labour), TCS / TDS, place of supply, reverse charge, godown, loyalty
  points.
- Payment: received now + mode, **earlier payments of this party to tick and link**, time set automatically.
- **⏸ Hold → Resume:** not in Vyapar. Starting a new bill while one is on hold asks: Resume / Keep & start new /
  Delete & start new.
- **✨ Fill with AI:** a photo or PDF of any bill fills the form, including PO / e-way / vehicle / UTR.
- **Entry warnings:** duplicate number, unusual price, credit limit, short stock, missing GSTIN / HSN. For a
  principal's invoice: the CI checks (§7).

**Bill copy / PDF:**
- Vyapar layout: Tax Invoice title, Original for recipient, item table + total row, CGST / SGST or IGST;
  Received / Balance, amount in words (Indian), HSN summary, place of supply, UPI QR + bank, terms, signature.
- Bottom bar (20-11): **WhatsApp & GST** (opens WhatsApp / SMS / UPI / GST files) · **Edit** · **⋮** (Duplicate, Delete);
  PDF / print / Share stay in the top bar. The bill copy itself keeps paise (GST bill).
- Actions:
  - Print (Android print), Save PDF, Share, WhatsApp / SMS, Bluetooth receipt;
  - GST files (e-invoice / e-way JSON);
  - Convert (quotation → order / invoice / PI), Duplicate, Edit, Delete;
  - **🔗 Link a Payment-In / Out** to this bill;
  - **📎 Supplier's bill copy** (purchase bills: Camera / Gallery / PDF).
- Tap any bill number anywhere → its copy.

**Notes:**
- Search, pin, party, remind date.
- **Photos / screenshots:** in the new-note form and in an opened note.
- **Replies:** comment or 📋 plan, with "By".
- **✓ Mark as done**: done notes go to the bottom.
- Ask Nexora can write a note (you confirm).

**Ask Nexora (business):**
- books tool: receivables, payables, unpaid bills, cash-bank, P&L + EBITDA, balance sheet, day book, party
  statement + sales in a period, GST, items, reminders, service, targets, online orders, dispatch, samples,
  complaints, commission, notes;
- "no such party" + closest names, business chips, Hinglish, natural voice + conversation mode.

**Phone basics:**
- offline cache + offline bills, firm switcher, app lock, T&P badge, Info guide everywhere;
- **app lock privacy (20-11):** the lock is its own top window (Dialog) so bill copies / Ask Nexora / full-screen
  pages never show behind the fingerprint prompt; with app lock on, no recent-apps picture
  (`setRecentsScreenshotEnabled(false)`, Android 13+). Screenshots still work (bills are shared). Checked on the
  phone: window order fingerprint → lock → app. Not yet seen with a bill copy open when the lock comes;
- date format from settings;
- drop-downs never move the form (`MenuScrollStop`, see `Nexora20/docs/SCROLL_DRIFT_NOTES.md`);
- **menu on a short screen** (landscape, fold outer screen; height < 560 dp): the whole drawer scrolls as one with a
  slim header (before, header + Sign out left room for ~3 items and the sections looked missing). Checked
  sideways on the phone 20-11: all sections and Sign out reachable.
- **still to calm down:** Executive My Day / Order Desk; Distributor Orders → SO → CI and Company ledger screens.
- **lists (20-11, round 3):** `HopModuleScaffold` header = title + count, refresh icon, one ⋮ menu (`menu`
  param) — no instruction line; `HopListCard` soft card, no outline; `hopMoney` shows no ".00" on whole amounts;
  page titles sans (no serif next to sans). Catalogue: Import / Share / Select to delete in ⋮, one light line per
  item, zeros hidden.
- **settings wiring (20-11):** `util/HopSettingsSync` applies date format / paise / currency when a UI opens, when a
  screen opens and right after a save (+ "Saved" toast); Company profile save updates the firm name in the top bar,
  menu and firm list at once.
- **less clutter (20-11, round 2):** screen titles have no explanation line (the ⓘ guide has it); top bar shows only
  the firm name; ✨ AI insights is one slim line until tapped; Ask Nexora: plain ✕, one welcome with 4 suggestions
  for that UI, no second greeting / rainbow chips, calm navy background without stars; full-screen pages keep clear
  of the status bar.
- **breathing space (20-11):** one spacing scale `NxSpace` in `ui/theme/Theme.kt` (screen edge 20, section 24, between
  cards 12, inside cards 18 × 16, 112 under the last item for the robot / + button); text follows the phone's
  font-size setting only up to 1.0× (`MAX_FONT_SCALE`). Reports and Settings are grouped cards with thin lines (no
  box per row / box in a box); Home tiles have no "Open ›". Use `NxSpace` for new screens.
- camera asks for permission first;
- the keyboard hides the bottom bar / robot / FAB.

## 7. Distributor UI (the distributor's own firm, same books engine)

Owner's choice (19-11): the distributor runs **their own business**.
**Own firm (20-11):** the Distributor UI works in its own workspace `<base>__dist` ("My distribution firm" until the
Company profile is filled), made the first time it is opened. Its items, Article Master, bills, parties, notes,
settings, Guard, Ask Nexora memory are all apart from the Business UI. Older distributor test entries made on 19-11
stay in the Business Test firm (no data moved by script). The company (brand) they buy from is a
**principal** supplier (`hop_vendors.is_principal`). Phone: `HopMode.distributor`, its own menu + Home.
Server: `app/hop_distributor.py`, `app/hop_dist_extra.py`, routes `/api/v1/hop/dist/*`.

**Header:** "Distributor · NEXORA". **Menu:**
1. **Company (orders, SO, CI):** Orders → SO → CI, Companies & terms, Article Master, Purchase Order, Purchase Bills (= company
   invoices), Company ledger & payments, Payment-Out, Purchase Return.
2. **Retailers:** Sale Invoices, Delivery Challan, Sale Order, Payment-In, Retailer payments & follow-up, Goods per
   retailer, Receivables, Sale Return, Dispatch.
3. **Warehouse:** Warehouse, Article Master, Stock, Godowns, Reorder stock.
4. **Forecast & targets:** Forecast & what to order, Targets.
5. **Money & reports:** Business profitability, Cash & Bank, Expenses, Reports (all), GST reports, Print many bills,
   Export to Tally.
6. **Staff:** Staff performance, Team duty, Staff permissions, Audit trail.
7. **Settings:** Company profile, Settings, Print theme, Theme.

**Screens:**
- **Home:**
  - navy greeting card with the company name, NEXORA Guard alerts;
  - cards: orders waiting for SO, SO waiting for CI, to pay the company, overdue to the company, company invoices
    this month, my sales this month, retailers owe me, stock value, order now, out of stock, retailers to follow up,
    profit;
  - top retailers, ✨ AI insights.
- **Orders → SO → CI:**
  - Set company. Filters: Open / Ordered / SO received / Part billed / Billed / All.
  - Each order shows its SO and CI, days waiting, and the amount not billed.
  - + New order, **+ SO received** (✨ AI reads the SO PDF / photo; **✨ SO check** against my order + the Article
    Master before saving), Link CI, SO without order.
- **Article Master:**
  - code, name, category, season, MRP, DP (buying price), price to retailer, margin %, stock, price history;
  - **✨ Read company price list — any company sheet** (`app/hop_article_sheet.py`, 20-11): finds the header row
    anywhere, lists every column with samples and its role (name part / code / category / season / MRP / DP =
    Ex-Mill / PTR / GST / HSN / unit / **detail** / skip). Every other column (TC, size, shade, ply, bale pack, MOQ,
    margin %…) is kept on the article as a detail (`hop_products.custom_fields`, searchable). Section rows
    ("Fleece Collection") become a Collection detail; brand / product cells copy down; order-qty columns are skipped.
    Names are built: "Aster 100 TC DB BS 224 X 244"; clashes get the telling detail added. Roles are remembered per
    sheet layout (`hop_dist_sheet_maps`). **✨ AI match columns** (one capped call). Row checks: PTR / DP against the
    sheet's own MD / MU % with or without GST. PDF / photo still read by AI. Full-screen preview on the phone; big
    jumps and ⚠ rows start unticked. Checked on 3 real AW26 sheets (Bedsheet 44, TOB 35, Towel 80 articles).
  - AI master check: no DP, no MRP, above MRP, below cost, code used twice.
  - **Price changes** (banner on the master, a badge on each card such as "DP ▲ 10% ₹150 → ₹165", a chart in the
    article sheet, and its own page):
    - filters: 7 / 30 / 90 days / 1 year, and DP / MRP / to retailer;
    - totals: number of changes, up / down, the effect on stock value;
    - **✨ margin alerts**: DP went up but the retailer price did not → the price that keeps the old margin, set with
      "Use ₹…" after you confirm;
    - average change per category, a timeline by day, AI insights;
    - Ask Nexora `dist_price_changes`.
- **Companies & terms** (20-11, many brands — `app/hop_dist_terms.py`, `/dist/companies*`, phone `HopDistCompanies.kt`):
  - one card per company: payment terms, credit days, cash discount %, lead time, other terms, articles, how many
    are off the rule price;
  - **price rule** for the price to retailer: mark-up on DP (DP × (1+p%)), margin (DP ÷ (1−p%)), mark-down from
    MRP (MRP × (1−p%)), rounded to ₹1; an article can have its own rule (article form) that wins;
  - **✨ Check prices**: articles not at the rule price, ⚠ above MRP (kept at MRP) / below DP start unticked → tick →
    "Use the rule price" (you confirm; price history keeps the old price);
  - articles carry their company (`hop_products.vendor_id`): article form Company chips, Article Master Select →
    Set company, price-list preview "This sheet is from"; with one company, loose articles are its own;
  - ✨ AI insights `company-terms`, Ask Nexora `dist_company_terms`.
- **Brand desk** (20-11, `app/hop_dist_brand.py`, `/dist/brands`, `/dist/brands/<id>`, `/dist/shipments/<bill>`; phone
  `HopDistBrandDesk.kt`, first item of Company): one page per company — chips (⚠ late / overdue), dark card with
  terms + rule (tap = edit), setup checklist (terms, rule, articles, first order, SO, CI — each says where to fill),
  tiles (open orders, SO waiting for invoice, goods still to come, in transit, to pay, overdue), **goods still to
  come** per article (SO qty − qty on linked CIs), **shipments** (LR, transporter, vehicle, dispatched / expected /
  received, late), latest orders, unpaid invoices. ✨ AI insights `brand-desk`, Ask Nexora `dist_brand_desk`.
- **Bulk import** (20-11, no row limit — `app/hop_dist_bulk.py`, `/dist/bulk/*`, phone `HopDistBulkImport.kt`, menu
  Company → Bulk import + button on Orders): SO / CI / credit note / debit note from Excel, CSV, PDF, photo, ZIP or
  RAR (server `unar`). Excel is read free: header row found anywhere, columns guessed / remembered per layout /
  ✨ AI match columns, rows grouped by document number (blank number = same document). PDFs / photos wait →
  ✨ Read all with AI (1 read each, count shown). Preview: company pick, sheets + Columns, documents tick / untick,
  ✨ Check all (Article Master rate / GST, CI vs SO, totals), already-saved numbers skipped. Save: SO → `hop_dist_so`
  (linked to my PO by its number); CI → purchase bill (stock in, linked to the SO); CN → Purchase Return / Dr. Note
  (amount-only lines move no stock); DN → purchase bill "Company debit note". Sheets kept in the job until saved
  (jobs cleared after 2 days). Tests `tests/test_hop_dist_bulk.py` (3,500 SOs in one sheet). Server module started by
  Claude, routes + first screen by Cursor, screen redone + roles/company/checks routes by Claude.
- **GST by category** (20-11, `hop_dist_extra.category_gst_overview / fill_category_gst`, `/dist/category-gst*`, phone
  `HopDistCategoryGst.kt`; menu Warehouse + button in Article Master): per category articles, without GST, rates used,
  without HSN, ⚠ mixed rates; tap a GST slab (0 / 0.25 / 3 / 5 / 12 / 18 / 28 / 40) = default; ✨ suggested = the
  rate most of its articles carry; "Fill N" puts the default only on articles with NO GST (you confirm).
- **Company ledger & payments:**
  - billed / paid / to pay / overdue, unpaid company invoices with overdue days, ledger with running balance;
  - Pay the company;
  - **✨ Check with the company's ledger** (their statement as PDF / photo / Excel): missing invoices / payments,
    amount differences.
- **CI checks:** a purchase bill from the principal is checked against its SO / order and the Article Master — qty,
  rate, GST, duplicate CI no. — through the normal entry warnings.
- **Retailer payments & follow-up:**
  - to collect / overdue, **✨ Call today** (priority from overdue, age, broken promises, days since follow-up);
  - age buckets 0-30 / 31-60 / 61-90 / 90+;
  - a retailer sheet: Call, WhatsApp reminder, unpaid bills, add follow-up (call / visit / WhatsApp / SMS, note,
    promised date + amount), history with kept / broken promises, payments.
- **Goods per retailer:** by dates and search; per retailer: items, qty, amount, last bill. Challans count as goods,
  not money.
- **Warehouse:** stock value, out of stock, item list, What to order / Godowns / Stock entries.
- **Forecast:**
  - look back 30 / 90 / 180 days, keep stock for 15 / 30 / 60 days;
  - per item: stock, on order, sale per day, days left, trend, lead time, order qty + order-by date;
  - **✨ Make the suggested order** (you check, then a PO is saved).
  - **By retailer** tab (20-11, `hop_distributor.retailer_forecast`, `/dist/forecast/retailers`): per retailer the
    top items, every-N-days rhythm, usual qty, last / next expected order; "Who needs what" (due now / this week,
    tap to call); ✨ AI insights `retailer-forecast`; Ask Nexora `dist_retailer_forecast` ("Sharma ko kya chahiye hoga").
- **Staff performance:** per login — bills, sales, collections, orders to the company, challans, follow-ups,
  promises, edits, deletes, active days, tips.
- **Business profitability:** sales, cost (qty × DP), gross profit, margin, expenses, net; month by month; tabs
  Items / Retailers / Companies / Below cost; AI check.
- **UI walls in Ask Nexora** (`nexora_ai_agent.tool_allowed`): Business / Distributor never get Executive tools and
  `read_app` only reads `/api/v1/hop/*`; Executive never gets the books. Contacts search only that UI's tables
  (`nexora_contacts.UI_TABLES`), chat turns + learned questions are keyed per UI (`_ui_ws`), a proposal is saved and
  confirmed only in the UI where it was asked (`ask_nexora_actions.ui / firm`), Guard's inbox follows the UI.
- **Ask Nexora (distributor):** `dist_orders`, `dist_company_ledger`, `dist_retailer_goods`, `dist_forecast`,
  `dist_warehouse`, `dist_overview`, `dist_articles`, `dist_retailer_dues`, `dist_profit`, `dist_staff`.
  Chips: "Kya order karu", "SO / CI status", "Company ka hisaab".

## 8. Settings — every setting

**Business / Distributor → Settings (bills, GST, print)** (`app/hop_app_settings.py`, the same list on web + phone):
- **Transaction:**
  - number prefixes (Sale Invoice, Estimate, Sale Order, Challan, Proforma, Payment-In);
  - round off to ₹1, rates include GST, default due days, cash sale default;
  - when stock is not enough (warn / block / off);
  - keep the supplier's bill copy with every purchase bill.
- **Taxes & GST:** composition scheme.
- **Print:**
  - amount in words, bank details, signature, terms;
  - paper size (A4 / A5 / thermal 80 / 58), copies (original / duplicate / triplicate);
  - date format, amounts with / without paise, footer, header line;
  - HSN column, HSN-wise tax summary, page margins, currency symbol.
- **Sharing:** UPI ID + name, "Pay by UPI" on bills, WhatsApp texts (bill, service reminder, payment reminder).
- **Reminders:** morning email of money to collect, auto email reminders to customers, days before due, repeat every
  N days, daily email backup.
- **Party:** party groups, loyalty points (₹ per point, point value, from date), credit-limit action.

**Other settings:**
- Company profile (name, GSTIN + Verify GSTIN AI fill, state, address, logo, signature, bank, UPI).
- Print theme (12 colours). Theme (premium palettes: Royal Sapphire, Midnight Navy, Royal Plum, Deep Teal, Emerald
  Royale, Olive Sage, Ivory & Gold, Copper, Burgundy Velvet, Champagne Rose, Pearl White, Onyx).
- Recycle bin, Updates (per UI).
- Backups: locked `.kunwar` file, server backups, restore into a new firm / replace.
- Multiple firms.
- Government & approvals: e-invoice IRN, e-way bill, GSTIN check, SMS / DLT, payment links — built, switched on after
  the owner's approvals.
- App lock, T&P list.

**Executive → Settings:** profile, Google Drive, mail sync, language, customization, updates, Ask Nexora voice,
Galaxy Watch, plans & billing, manage users, share NEXORA.

## 9. AI layer (shared by all UIs)

- **Readers:**
  - bills (✨ Fill with AI), SO PDF, company price list, company ledger (SOA);
  - credit notes, visiting cards, party ledger (Executive Hisaab).
- **Checks before saving:** bill entry warnings, SO check, CI check, article checks, Guard rules.
- **NEXORA Guard:** rules → inbox → ✨ Explain & fix, ✨ Today's AI note (`app/services/nexora_guard.py`).
- **✨ AI insights:** 3–5 points on reports and on every Distributor screen (`hop_ai.report_insights`, one capped
  call).
- **Ask Nexora:**
  - Gemini agent with tools; prompts per UI (`X-Nexora-UI`: executive / business / distributor);
  - `propose_action` → the user confirms;
  - voice (natural Hinglish TTS), learning of question shapes;
  - daily limits (50 AI questions, 100 voice clips per user).
- **Cost rules:** `maxOutputTokens` on every call, usage logged in `ai_agent_usage_log`, ask before tests over ₹10.


**Edit profile batch (21-11 evening, server `7038130`, phone `76df678`, OTA 2.0.467225):** logo letters use
`FirmInitials` (Kunwar home → KH); GSTIN row with a Fetch button + hint (auto fetch at 15 characters, Fetch fills
address / state / PIN / PAN again, never the business name); Business type (28) and category (70) open a search picker,
"+ Add “…”" keeps a name not in the list ("Other" removed); IFSC → bank name + "Branch & address" (new column
`hop_firm_profile.bank_branch`, printed on bills as "Branch :") via `/api/v1/masters/ifsc-lookup` (any login, every UI;
`app/services/ifsc_lookup.py`, public IFSC directory, 7-day cache) — also in Cash & Bank → New bank account (fills the
account name when empty); Banking fields one per row; signature pad: full-width "Use signature", Clear / Cancel below.


**My brands + AI batch (21-11 night; server `3c6182d` → `c64c8a4` deployed, phone `90ed27d` + `492d497` committed, NOT
built yet — build only when the owner asks):** Brand desk renamed **My brands** = registration only: brand cards (letters,
GSTIN, city, contact, "Setup x of 3" / "Registered ✓"), tap → navy terms card + Company details card (call / WhatsApp /
mail, Edit) + 3-step setup (details, terms, price rule; `hop_dist_brand.brand_desk` steps) with Fill ›; orders / goods /
shipments / unpaid invoices moved to "Orders, goods & payments of this brand ›"; + Add brand opens terms right after.
Add a brand form in three parts (Brand / Address / Contact). Terms form: short title, rules as a list. ✨ AI: photo
(visiting card / letterhead / GST certificate / cheque) fills Add brand, Company details and Edit profile —
`/api/v1/masters/read-company-photo` (`app/hop_company_card_ai.py`, one capped call, GSTIN + IFSC checked free, nothing
saved); My brands AI insights key `my-brands` (`hop_dist_brand.registration`); Ask Nexora `dist_my_brands`, `ifsc`.
Also: profile Save turns off after saving + scrolls to top; all plain placeholders light (30%); note form wider with
one-line Camera / Gallery. Pre-existing failing test: `test_nexora_guard::test_hop_checks_find_real_problems` (reuses
bill number MT-77, refused since #32) — fix the test data.

## 10. Pending / open

**21-11 afternoon batch (Distributor notes, number order):** #10 many photos on a note (pick many at once; before, only
the last stayed — a stale-list bug in `HopPendingNotePhotos`; existing notes now take many too). #16 bill form: a
scanned code not in the items opens "New item" (barcode filled, price / GST / HSN) → saved (offline too) and the line
added; Scan many asks for each new code. #17 logo initials also in the Hub firm list (`PlanGate.InitialAvatar` →
`FirmInitials`). #37 Article Master / Catalogue field "EAN / barcode"; the price list reader has an "EAN / barcode"
column role (EAN / barcode / UPC / GTIN, before "code"). #2 checked — GSTIN fetch is on every GSTIN field. Photos
came out turned (landscape ↔ portrait): `HopProductPhotoApi.shrink` now turns by the EXIF orientation before
re-saving (product, note, AI photos). **Customer care feedback** (`app/hop_feedback.py`, table `nexora_feedback`,
screenshots `feedback_shots/`): ⓘ guide of every screen → Report a problem / Suggest a change (screenshot of that
screen, screen key, UI, firm, app version) → `POST /api/v1/hop/feedback` (offline kept) → email to Customer care
(`NEXORA_FEEDBACK_EMAIL` or the platform owner's login) + `GET /feedback/inbox` (platform owner only) →
`POST /feedback/<id>/reply` {status, reply} → the user sees it in Menu → Help & feedback (badge from
`/feedback/counts`). Users only ever see "Customer care". No floating button (owner: not professional).

**Bill numbers + offline bills (21-11, Distributor notes #32-#35):** `app/hop_bill_numbers.py` — a number belongs
to one bill (live or CANCELLED); `check_free` refuses a typed / offline number already used (create, edit,
restore); deleted bills free their number (`next_manual_doc_number` and `offline_series` count only
`HOLDS_NUMBER` rows) so "bill 2 deleted → next is 2"; restore is refused when the number went to another bill;
`POST /party-transactions/<id>/cancel` {reason} = soft delete + `cancelled_at` / `cancel_reason` (stops counting,
keeps the number), the bill list (`txn_types`, not a party ledger) shows it with status "Cancelled — reason",
balance 0. Phone: `HopOfflineCache` never keeps `/next-number`; `HopOfflineNumbers` refresh never counts backwards
(max of server and phone), `peek` shows the offline number in the form when offline, a phone-made series
`OFF/U<id>-<type>-n` when never fetched, `giveBack` when the last waiting bill is deleted; series refreshed on every
module open while online. `HopOfflineBillsBar` on top of every bill list: waiting bills (number, party, amount,
error) with Share (text) and Delete. Preview Delete dialog → Cancel bill / Delete / Keep.
#34 (owner: offline, Delete kept spinning, no back / menu / drawer): the dialog checks the internet first ("No
internet — the bill is not changed"), calls time out after 20 s, Keep / back always close it (the job is cancelled);
`withColdStartRetry` stops at once on `UnknownHostException` (no internet) instead of retrying 45 s — every screen.
**Offline everywhere (owner 21-11: "koi kaam nahi rukna ... delete, new, old sab screen pe dikhna chahiye ... seedha
update karo"):** phone `HopOfflineCache.Interceptor` — every POST / PATCH / PUT / DELETE under `/api/v1/hop` made with
no internet (or that never reached the server: UnknownHost / Connect) is kept as the exact request
(`HopOffline.enqueueRaw`, same order, own UI + firm) and answered "ok"; `HopOffline.flush` replays it (method + body,
`X-Nexora-Replay`), a 404 on edit / delete = already gone. Every list / detail GET (saved copy offline, or live while
something waits) is drawn with the waiting changes applied: DELETE → row gone, cancel → "Cancelled", JSON PATCH / PUT
→ fields merged, JSON POST to a list → new row. No "waiting" labels anywhere; offline bills look like normal bills
(Share / Delete); the drawer card shows only changes the server refused. Server-answer calls (AI, GSTIN, imports,
backups, restore) say "No internet — this needs internet" at once. New bills / notes keep their own queues.

**Scan many (21-11, QnA step 1 of 3 — fast entry #42 / photo #43 / voice #44; owner checks the note numbers):**
server `hop_items.scan_index` → `GET /api/v1/hop/products/scan-index` (items + `keys` barcode / lower-case code →
index; services and deleted items left out; the phone's offline cache keeps it) and `hop_items.scan_check` →
`POST /products/scan-check` (counted vs book stock, codes not in the master, warnings; rule based, no AI cost).
Phone: `HopBarcodeScan.scanMany` + `HopScanActivity` EXTRA_MANY — the camera stays open, beep + vibrate per piece,
same code counts again only after it left the frame (`ScanGate`: 900 ms gap, 600 ms cooldown), Undo / − / +, Pause,
Light, Done. Bill form: "Scan" (one) + "Scan many" (all lines with qty; unknown codes listed). Article Master "Scan":
one piece → open / Add with the barcode; many → "Counted · n pieces" dialog (books vs counted, Add for unknown codes,
nothing saved by itself). Google's own scanner screen is not used (its branding cannot be removed); NEXORA uses the
same ML Kit engine on its own screen.

**Photo → new product (21-11, QnA step 2):** `app/hop_product_ai.py` → `POST /products/read-photo` (file + the EAN
the phone read with ML Kit): EAN already in the master → that item, no AI; else `hop_ai.read_product_photo` (one
call, 400 tokens, daily limit, logged as `nexora_agent_hop_product_photo`) reads name / brand / category / MRP / size
/ design / colour / material / HSN / EAN; GST from `app/hop_hsn_gst.py` (Ch 50-64 textiles / apparel / footwear,
5% up to ₹2,500 a piece else 18% for made-ups, apparel and footwear; MRP used as the price, flagged) else the
category default; look-alike items by photo; checks. `hop_product_photos`: `photo_hash` (dHash of the middle 70%,
saved with every photo, filled for old photos on the first match) and `match()` → `POST /products/match-photo`
(rotations tried, score 0-100, ≥ 40 shown, top 3). Phone (`HopProductPhoto.kt` → `HopPhotoProduct`,
`rememberProductPhotoTaker`, `HopPhotoMatchDialog`): Article Master "✨ Photo → new" (look-alikes → pick or New
article → form filled, Save → photo uploaded) + "Find by photo"; bill form "Photo" → pick → line added.

**Voice bill (21-11, QnA step 3):** `app/hop_voice_bill.py` → `POST /party-transactions/voice-draft` {text,
alternatives (the phone's 5 speech guesses), kind sale|quote|purchase}: one capped AI call (400 tokens, logged as
`nexora_agent_hop_voice_bill`) splits party / item / qty / rate / unit and may "pick" a name from a shortlist of THIS
UI's items (40) / parties (15) that sound like the words — a name not in the list is ignored. Matching
`app/hop_name_match.py` (words + sound keys for Hinglish: "aaster dabal" = "Aster Double", "flowra" = "Flora",
"sharmaa" = "Sharma" + learned words `hop_voice_aliases`, `POST /party-transactions/voice-learn` when the user taps an
option). Answer = ✨ Fill with AI shape + `options` / `party_options` / `say`. Phone `HopVoiceBill.kt`
(`HopVoiceBillBar` in the bill form under ✨ Fill with AI): SpeechRecognizer en-IN, prefer offline, editable text,
"Add more", ✨ Make bill, amber chips, TTS reads "party, n items, total ₹x. Correct?". **Ask Nexora** gets the same
ears: `hop_ai._similar` now also scores by sound (every party / item lookup, and ✨ Fill with AI matching), learned
party words win in party statements, and an item search with no hit falls back to sound match (`heard_as`).

**Now doing (20-11 late night):** Cursor finished (7)/(12)/(13) + GSTIN address/PIN deploy + APK; CLAUDE.md
updated under Cursor's name. Still: app Notes reply+done as **By: Cursor** when phone ADB is back; Claude =
calm screens; (17) payroll plan skipped; real barcode / bulk file / PIN field recheck on phone.

**Open `claude` notes — read on the phone 20-11 ~23:15 (owner: "open notes of business and distributor, note on
claude.md, ask me what to do first"). Waiting for the owner to pick the order:**
- Distributor UI:
  - (D1) DONE in code 20-11 (`hop_firms.move_ui`, `/firms/move-ui`; phone `HopBusinessName` per UI, firm list → "Move … to Distributor UI…"): cause = firms made before 20-11 had no UI and were all filed under Business; "kunwar home" is such a firm in the Business firm list. The owner moves it in the app (no script). Was: **Firm name leak:** "Kunwar home" is the Distributor firm's name but shows in the Business UI — owner:
    "saare kaam chodke pehle yeh lock lagao", no login / AI / notes / anything of one UI in another.
  - (D2) DONE in code 20-11 (`ui/util/TypeAhead.kt`: bill form party field searches name / mobile / GSTIN / city; items match every word). Was: Type-ahead search: party suggestions on every key stroke (bill form party picker), same for items in the
    bill and in Article Master.
  - (D3) DONE in code 20-11 = Brand desk (see §7). Was: Many brands end to end, neat and simple: where to fill each brand's details and margins, link Article Master
    to its products, tally SO / PO with my orders, CI / tax invoices / bills received, payments, shipment tracking,
    received vs pending, billed vs pending (Companies & terms + Bulk import + Orders → SO → CI cover part of it).
  - (D4) DONE 20-11 (phone `HopNoteEditDialog`): N opens the full note form (party type-ahead, remind, photos, "For Claude" tick — no party → party "claude", with a party → body starts "[For Claude]"; Claude must also read those). No N on the Notes page (B2). Was: Quick note ("N") has no party tag and is not the full note form — make N open the full note.
  - (D5) DONE 20-11 (phone `ui/util/FirmInitials.kt`, up to 3 letters, skips "and / pvt / ltd"): letter logo in the menu header + bill preview when the firm has no logo picture; "N" note button inside full-page forms (`HopFullScreenPage`: bill, quotation, company profile, Vyapar import, distributor forms) and the party form. PDF bill still shows no letter badge (only a real logo). Was: Logo = first letter of each word of the firm name ("Kunwar home" → KH); N button inside forms too.
  - N note form 📸 (20-11, phone `HopScreenshotMarkup.kt`): the window N sits in is drawn to a picture the moment N is tapped; "Take screenshot of the screen behind" opens it, finger draws red lines (Undo / Clear), "Add to note" puts it in the new note's photos. D5 replied + marked done in Distributor → Notes.
  - Notes done / raise again (21-11, `hop_notes.py` `_status_line` / `_link` / `is_for_claude`, route `_is_founder`; phone `HopNotesScreen` `ClaudeNotes`, `NoteLink`, `hopScreensForLink`): Mark as done (Claude or the user) asks "Where can it be checked?" (a screen of the open UI) → history line "✓ Marked done · by · date" + "Open ▸ screen" (HopMode.navigate). ↩ Raise again asks what is still wrong, clears ✅; a non-Claude reply in a done note also raises it again. Row shows "For: Claude" and "↩ Raised again". N button + "For Claude" tick only for the founder account; server refuses notes for Claude from others (403). 📋 Plan chip now reads "Plan ✓" and the button "Send plan".
  - Note numbers (21-11, `hop_notes._NO`): #N = count of the firm's notes up to this id (deleted ones included, so numbers never move); search "#12" / "12"; phone shows #N on the row, the note title and "Edit note #N"; the edit form now shows the saved party (also "claude").
  - Terms book (21-11, `hop_dist_margins.py`, phone `HopDistTermsBook.kt`, menu Company → Terms book): margin chain per article MRP → perceived % → retailer % → GST step (retail / awd / none) → my mark-up % → ex-mill; default per company + category (`hop_dist_margin_defaults`, read live), article own terms (`hop_products.mg_*`, `mg_from=own`); asks "N articles follow this default" before a change; ⚠ when saved DP / PTR differ; Save PTR / ex-mill on ticked rows (price history). + Add company (`/dist/companies/add`: name, GSTIN with Fetch, address, state, contact → supplier + ticked as company; same name / GSTIN reuses). New Distributor article without a company → NEED_COMPANY → form shows "+ Add company" and Save again. AI insight `terms-book`, Ask Nexora `dist_terms_book`.
  - Goods received (21-11, `hop_dist_receive.py`, phone `HopDistGoodsReceived.kt`, menu Company / Warehouse → Goods received): a purchase bill / CI from a principal company saved from now on gets `stock_mode='receipt'` (hop_money `_post_stock(new=)` → `waits_for_goods`) and moves no stock; receive per article (part ok, max = still to come) → `hop_dist_receipts` + lines, stock journal `grn:<id>` with `vendor_id`, `doc_txn_id`, `so_id`; full receipt sets the shipment's received date; Undo takes a receipt back; old bills keep stock on save. Stock by company card. CI lines not in Article Master listed. Bill stock lines now also keep `doc_txn_id` + `vendor_id`; Article Master hand stock writes a `manual:article` journal line.
  - Notes ✓✓ (21-11): founder-only `checked` on a done note for Claude (`checked_at/by`, history line); delete / bulk delete of a Claude note only after ✓✓; raise again or a user reply clears it.
  - Night batch 21-11 (`hop_dist_company.py`, phone `HopDistCompanyScreens.kt`): Warehouse row → item card (company, prices, terms, godown stock, still to come, sold 90 days, last stock lines with Goods received / Bill / By hand + company); + Add / − Reduce stock (`manual:dist` journal with `vendor_id`; adding needs a company or "Opening stock"; reducing needs a reason). Company details edit (Brand desk → Edit details). Company report: flow (ordered txn 66, CI txn 2, received from receipts, to come), sales / cost / gross margin by category of the company's articles, CD per CI (company CD % × invoice, pay-by = date + credit days, earned / lost / open), incentives (`hop_dist_incentives`, % of a base or amount, linked to a company credit note txn 16) → net margin. Terms book "✨ Margins from the company sheet": % columns kept as article details (retailer margin / mark-down, AWD mark-up, perceived) → own terms, GST step matched to saved MRP / PTR / DP. Cash & Bank "+ Add account" button next to New bank entry (B3).
  - UI 21-11 (owner feedback on the phone): Ask Nexora robot sits inside a revolving teal → purple ring in the centre of the bottom menu (`ui/util/NexoraRingButton.kt`, `AskNexoraUi.open`) in Business, Distributor and Executive — the floating robot is gone there (Admin keeps it). Article Master calmed (one main button + 4 short ones that fit, price-change card without "(s)", AI check shows 1 point + "+N more", lighter article cards). `ui/util/NxDropdown.kt` — a calm pick-one box used for category / company lists (Article Master, Terms book, Company report, Goods received). Item card is a full page. App lock (PIN) has its own menu item in every UI; it is OFF on the owner's phone until the owner says.
  - (D6) GSTIN Fetch in every UI — built by Cursor, to check + reply.
  - (D7) "GST kyu nahi dikha raha" — GST by category screen built (`c1306e7`, not deployed yet), to reply.
  - (D8) Barcode scan → item or Add new, manual stock — built by Cursor, to check + reply.
- Business UI:
  - (B1) Status bar: time and signal icons are invisible with the theme.
  - (B2) Notes page: the floating N is not needed there (the page has + Note).
  - (B3) "This screen is boring and Add new account is not visible" (likely Cash & Bank — check).
  - (B4) Full payroll like INDPayroll — reply with a plan (needs + how to test with data).
  - (B5) Partners / team roles plan — still waits for the owner's 3 answers.
  - Untagged (left alone): "yaha pe add new ka option bhi do agar user ki profile kuch or huyi to" (tag not seen —
    check before working on it), "QA test note rates bhejne hai".

**Next (20-11 late night, session paused for usage):** D5 (KH initials logo + N inside forms), then D6–D8 replies. Server NOT yet deployed for: GST by category, firm move (D1), Brand desk (D3) — ask the owner first. Phone has all of it installed. After deploy: owner moves "kunwar home" via Business firm list → Move … to Distributor UI.

**Claude 20-11 late night:** (6) Companies & terms + (13) bulk import built, server deployed (`40c4a06`), phone
installed, both notes replied + marked done in Distributor → Notes. Bulk import checked on the phone only as far as the
screen opening (no company ticked in the Distributor firm, no real file tried yet). Open notes left for Cursor to
answer: (7) barcode scan, (12) GSTIN Fetch, GST category screen. (17) payroll plan still to answer.

**Now doing / next (written before the work — owner 20-11: "pehle claude.md pe likho kya karna hai"):**
1. **Notes of every UI** — read 20-11 evening (17 `claude` notes); owner picks the order before work starts:
   - Distributor → Article Master / prices: (1) Price changes counts first prices of new articles as "changes" —
     show them apart; (2) Price changes needs search + filter; (3) GST shows 0 — GST per article + default per
     category; (4) HSN missing + "+ field" for own details kept the same across all articles; (5) change badge on the
     card should show MRP, not DP; (6) many brands: per company terms + per product margin / mark-up / mark-down;
     (7) barcode: scan → item or "Add new", manual stock, barcode in the master; (8) bulk delete in Article Master.
   - Parties (all UIs): (9) Call + WhatsApp on every party card and next to the mobile no.; (10) similar-party check
     matches on "SH" (Shri) → wrong; (11) party Excel import drops the firm name; (12) GSTIN Fetch in every UI.
   - Imports: (13) bulk import with no limit for SO, CI, credit notes, debit notes.
   - Notes / testing: (14) many photos per note; (15) select + delete many notes; (16) floating "N" button on every
     page of every UI (testing phase) to write a note from there.
   - Business note: (17) full payroll like INDPayroll — reply with a plan (needs + how to test with data).
   Untagged notes are left alone; partners note still waits for the owner's 3 answers.
   **Done by Cursor 20-11 (reviewed by Claude, fixed + committed):** (1) first prices kept apart + (2) search /
   Changes · First prices · All filter on Price changes; (3) category GST default (only fills an EMPTY GST — fix);
   (4) HSN, barcode, stock and "+ field" in the article form, firm-wide field list on every article (fix); (5) card
   badge prefers MRP; (6) per-company margin / mark-up / mark-down + terms on the server only (no screen yet);
   (8) Article Master bulk delete; (9) Call + WhatsApp on party rows / details; (10) honorifics: only LEADING
   Shri / SH / Smt / Mr / M/s dropped — initials like "S M Traders" kept (fix); (11) party Excel import prefers
   "Firm name"; (12) GSTIN Fetch in the bill form's quick add; (14) 40 photos per note; (15) notes bulk delete;
   (16) quick-note "N" button on Business + Executive screens, notes tagged claude + "[UI]" (fix). Stock in the
   article form is sent only when changed (fix). Server deployed + phone installed 20-11 evening; checked on the
   phone: Price changes 40 real changes (was 559) with First prices apart + search; Notes "Select"; "N" button.
   **Notes answered on the phone 20-11:** 11 replied + marked done (search/filter, first prices, MRP badge, many
   photos, bulk delete, Call/WhatsApp ×2, SH match, Excel firm name, HSN + field, N button); 5 replied with status and
   left OPEN: (6) company terms screen, (7) barcode scan → item / Add new, GST category default screen, (13) bulk
   import with no limit, (12) GSTIN Fetch on Parties in every UI. (17) payroll note (Business) not answered yet.
   **Done + deployed by Cursor 20-11 night (own SSH key `nexora_server_cursor`; Co-authored-by Cursor):**
   - (6) Company terms screen already `DistCompaniesScreen` (no extra UI from Cursor).
   - (7) Article Master **Scan barcode** → open item or Add new (prefill); Scan on article form. Phone: scanner
     screen opens (real barcode read not yet tried).
   - (12) GSTIN Fetch: `GET /api/v1/masters/gstin-lookup` (Executive) + HoP routes; bill quick-add; party forms.
   - (13) Bulk import SO/CI/credit/debit — `/api/v1/hop/dist/bulk/*` + phone `DistBulkImportScreen` (Orders →
     swipe → Bulk import). Real Excel/ZIP not yet tried.
   - Commits: `f57e510` (bulk + masters GSTIN) then `97a1eb5` (GSTIN address/PIN). Both pulled on Lightsail;
     `nexora` restarted. Server live on `97a1eb5`.
   - **GSTIN address / PIN (Cursor):** ClearTax was 401; owner gave `GSTIN_LOOKUP_API_KEY` (gstincheck) → set in
     `/etc/nexora.env` (not in repo). Parser now prefers `pradr.adr` full line (was wrong “0 Gurudwara…” from
     thin addr bits), returns `pincode` + trade/legal/city/state/status/constitution/nba. Phone: auto-fetch when
     GSTIN is 15 chars; fills firm, contact, address, city, state, **PIN code** field. APK installed 20-11 ~21:59.
     Checked on phone: full address + city + state OK; PIN field empty until that APK (then ADB dropped — recheck
     when phone reconnects). (17) INDPayroll plan still skipped per owner. Calm screens = Claude only.
   - **App Notes replies (Cursor):** phone was offline at end of session — when ADB is back, reply + mark done on
     open notes (6)/(7)/(12)/(13) + GSTIN PIN note as **By: Cursor**. Do not write live DB from scripts.
2. **Calm the last busy screens:** Executive My Day + Order Desk; Distributor Orders → SO → CI and Company ledger
   (same rules: title + count, soft cards, one light line, no ".00", folded rarely-used parts, ⋮ for extra actions).
   **Claude owns this** (owner 20-11: Cursor not to do calm screens).
3. **Salary first real use:** owner adds one staff member and pays once → check slip, Cash & Bank, Expenses, dues tab.
4. **Article Master with real sheets:** owner reads the 3 AW26 sheets in Distributor → Article Master and saves;
   clears old "Sheet Sets" items in the Business Catalogue (⋮ → Select to delete).
5. **Keep users interested (owner 20-11 idea, waiting for a go):** smart "Today" list on Home ranked by money and
   urgency (calls to make, bills due, stock to order, retailer due to buy, PF/ESI/TDS dates); one morning brief
   notification; weekly business health score; Home cards ordered by use; setup checklist with progress; "AI saved
   you N hours" wins; Ask Nexora day-based chips. No fake urgency or nagging.
6. **Ideas waiting for the owner's yes:** ✨ AI rate watch (PF / ESI / tax change alert, never auto-applied),
   Retailer UI first version, web parity (Distributor UI, Salary, Article Master columns, Forecast by retailer).



**Built, not fully checked, or waiting on the owner (19-11):**
- **Partners and team access:** the plan is in `docs/PARTNERS_AND_ROLES_PLAN.md` and in its note. Waiting on 3 answers:
  1. Is a partner equal to the owner?
  2. Shared notes for partners only, or for the whole team?
  3. Which roles first?
  Then build batch A.
- **Distributor:**
  - Try with real files (costs AI): a company price list, a company ledger statement, an SO PDF.
  - Not built yet:
    - the SO PDF file itself is not kept;
    - web screens;
    - schemes / claims;
    - follow-up "by" shows the login email (same as the audit trail).
- **Supplier bill copy:**
  - add one real supplier bill to check the upload;
  - next: ✨ Fill with AI keeps the scanned photo as the copy on Save.
- **Print many bills:** next steps for the owner to choose:
  - email the PDF to the CA;
  - Ask Nexora "last month bills PDF bhejo";
  - a cover page with totals.
- **Welcome screen:**
  - the status-bar icons stay dark;
  - Login in the same navy-gold style? (ask the owner);
  - debug preview: `adb shell am start -n com.princeoberoi.nexora20/.MainActivity --es nx_preview signup`.
- **Link payments:** no real link tried on the phone yet (Test firm has no free payment).
- **Not verified on the phone:**
  - a real barcode read;
  - a real print;
  - AI bill reader with PO / e-way / UTR;
  - Ask Nexora writing a note;
  - Delete on ops sheets;
  - Market Visit date display.
- **Retailer UI** is still only a preview ("under development") — no screens, settings or salary there yet.
- **Web parity** for the newest phone features (+ Salary, Forecast by retailer, Article Master columns): notes photos / replies / done, hold warning, link payments, bill
  copies, Print many bills, commission, targets switch, the whole Distributor UI.
- **Notes inbox:** 4 notes from 19-11 say "Done by User". The phone driver lives in the session scratchpad
  (`nx.py`, `typein.py`, `note_reply.py`, `groups.json`, `keys.txt` — copy them from the newest older scratchpad;
  `NXPIN` env = the NEXORA app-lock PIN, kept in Claude's private memory, never in this repo).
- **20-11 notes (all 3 done + marked done):** sign-up Send code only for a complete email; Distributor UI on its own
  firm (Article Master showed the Business catalogue — "Comforter", "QA Split AC" — because both UIs read the same
  firm). Server deployed 20-11; checked on the phone: Distributor shows "My distribution firm", empty Notes, 0 articles.
  `note_reply.py` fails on a long note (the By box is below the screen) — then tap By / reply / Send by hand.
- **Owner, 20-11 — breathing space:** every screen felt crowded ("ghich pich, bhari bhari, claustrophobic"). First pass
  done (shared parts + Reports, Settings, Home). Next: the remaining screens one by one (bill form, Distributor
  screens, Executive My Day / Order Desk), moving loose 6–8 dp gaps to `NxSpace`.
- **Phone connection:** the Wi-Fi debug address changes. Check `adb devices`; the mDNS name
  `adb-RZCY627Z4XD-…_adb-tls-connect._tcp` works.

**Owner's list for next sessions:**
1. Full T&P re-audit (every button, Business then Executive).
2. Study Vyapar on the owner's PC → field checklist.
3. Real barcode read.
4. Live AI tries (ask first).
5. Fold-phone layouts (owner to choose).
6. 3D / animation ideas (owner to choose).
7. Product photo AI.

**Bigger roadmap:**
- NEXORA remote support.
- Guard: daily server run + push notifications.
- Web parity.
- NEXORA website with policy pages → Razorpay partner.

**Cursor audit + guard rails (20-11):** Cursor (co-author line on 19 commits 12–15 Nov + `8a2c4a1` today) works
on this repo too. Live server check: code = main, settings untouched except the backup fix; but on 12-11 a script
DELETEd Ask Nexora chat rows on the live DB and on 13-11 there were 13 interactive DB sessions (content not logged) —
Claude or Cursor, same key then. Now: Cursor has its own SSH key (`cursor-agent`, fingerprint shows in the server
log); `.cursor/rules/live-server-safety.mdc` (always on) + `AGENTS.md` forbid live-DB writes from scripts, require
reading CLAUDE.md, own key, ask before deploy; old "Render auto-deploy" rules fixed (production = AWS Lightsail,
push ≠ deploy). Tell Cursor to use `~/.ssh/nexora_server_cursor`.

**Server backup fixed (20-11):** the nightly full backup failed 17-11 → 20-11 (SMTP_FROM in /etc/nexora.env
not quoted; the backup script `source`s that file). Quoted, fresh backup taken + checked. Keep every env value with
spaces or `< >` in double quotes.

**Waiting on the owner:**
- GSP account (e-invoice / e-way / GSTIN API);
- DLT + SMS provider;
- payment gateway KYC;
- off-server backups (S3);
- a remote for the phone repo;
- Executive target fiscal years.

## 11. How to work here

- **Server tests:** `python -m pytest tests -q` (full run ≈ 40 min; 1089 passed on 19-11 + the newer distributor
  tests). Write tests with a made-up company (`tests/test_hop_reports.py::co`, `tests/test_hop_distributor.py::firm`).
- **Phone build:** `./gradlew.bat :app:assembleDebug` (JAVA_HOME = Android Studio jbr); install with `adb install -r`.
  Long builds go in the background.
- **Deploy:** pull + restart on the server (`deploy/README.md`). Never write to the live DB from a script.
- **Every screen change:**
  - help text in `app/hop_help.py` + `app/hop_help_screens.py` (every "On this screen" line needs " — ");
  - the AI checklist row;
  - `docs/NEXORA_APP_MAP.md` for a new module;
  - this file.
- **Kotlin:** never write `/*` inside a comment (e.g. a route path ending in `/*`): it opens a nested comment.

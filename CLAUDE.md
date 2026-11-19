# NEXORA — what is built and what is planned

Read this first in every session. Detailed maps: `docs/NEXORA_APP_MAP.md` (every module, route, table, screen),
`docs/HOP_VYAPAR_PARITY_PROGRESS.md` (business-software batches + AI checklist), `docs/QA_NIGHT_2026-11-18.md`
(latest test log + open list), `AGENTS.md` (single-backend + AI rules), `deploy/README.md` (server, backups, deploy).
This repo is **public**: never put server addresses, keys, passwords, PINs or real business data in it.

_Last updated: 2026-11-19._

**Notes inbox:** the owner writes tasks in Business UI → Notes (Test firm) tagged `claude`. When the phone is connected,
read them, do the work, reply in that note (what was done / a plan) and **mark it done** — through the app only.

---

## 1. Owner's rules (always)

- **One backend for everything.** Phone, web and desktop use the same Flask `/api/v1/...` routes and the same
  workspace database. No phone-only business data. New feature = server route → phone **and** web.
- **Mobile first.** The phone app is the #1 priority; build and check on the phone first. Phone on USB / Wi-Fi debug
  → `adb install`, no OTA while the owner is the only customer.
- **AI in every feature** (never "later"): read documents into forms, smart suggestions, early warnings, AI insights on
  reports, and an **Ask Nexora** tool for every data area (Hinglish must work). **AI never saves by itself** — the user
  checks and saves (Ask Nexora uses `propose_action` → user confirms). Keep AI cheap: one capped call, inside the
  daily limit, usage logged. Fill the AI checklist row before calling a feature done. Ask before any test that costs
  more than ₹10.
- **UI text in English** (chat can be Hinglish).
- **Every UI shows the subscriber's own brand**, never "House of Prizm" / "PRIZM" for another business.
- **Never run bulk data scripts on the live DB.** Fix code; the user changes data through the app. Live-DB writes from a
  script are blocked on purpose.
- **Commit + push to `main` after every fix** (this repo). Phone repo has local commits only (no remote yet).
- **T&P (Tested & Passed)** marks (`app/qa_marks.json`, badge on phone + Settings → T&P list) only after **every
  button on that screen** was pressed on the phone on the latest build. Marks were cleared on 19-11 for a full re-audit.
- Don't touch: Wipe all data, Import old data on real data, Start day (location), Financial year close, Android
  permission prompts (the owner taps those).

## 2. The product

NEXORA = one login, several UIs (Hub page): **Admin Zone**, **Executive UI** (Bombay Dyeing distribution team),
**Business UI** (Vyapar-style billing + accounts for any business — "HoP" in code), **Distributor UI**, Retailer.
Stack: Flask + SQLite workspace DB (+ PostgreSQL platform tables) · web workspace `app/static/app.js` / `hop_app.js` ·
desktop = pywebview over the web app · Android app (Kotlin / Compose) in `AndroidStudioProjects/Nexora20`
(package `com.princeoberoi.nexora20`) · production on AWS Lightsail (Mumbai), systemd service, timers for daily jobs
and backups (details in `deploy/README.md`).

## 3. Built — Executive UI (Bombay Dyeing distribution)

My Day (greeting, Start day / attendance, NEXORA Guard alerts) · Customers (distributors + retailers master) ·
Article Master (189+ articles, MRP, season prices, history) · **Order Desk** (Filled Order ↔ SO ↔ CI, seasons,
pending SO, distributor hisaab, SO payment mails) · Distributor Zone (Payment status, Statement of Account from SAP GL,
Secondary sale, Grievances) · Mail sync (Gmail read-only: CI / SO payment import) · Target vs Achievement · Market Visit
(DSR, Excel import/export) · PJP (monthly plan, Excel import) · Call List · To-Do · Catalogues (Google Drive PDF / PPT) ·
Settings (profile, Drive, mail sync, language, customization, updates) · Galaxy Watch link · Ask Nexora (distribution
tools: visits, PJP, MRP ranges, party alerts, grievances, contacts — call / SMS / WhatsApp after confirm).
Full Executive UI screen check done 19-11 (every screen opens, no errors).

## 4. Built — Business UI (Vyapar parity + extras)

**Menu (8 sections, own brand header):** Dashboard · Parties · Notes · Sale & Billing · Purchase & Expense ·
Items & Stock · Cash, Bank & Accounts · Reports & GST · Sales team & Service · Staff & Attendance · Settings.

- **Sale & Billing:** Sale invoice, Estimate / Quotation (+ commercial quotation with sections), Proforma, Sale order,
  Delivery challan, Payment-In, Sale return / Credit note, Sale FA. Convert (quotation → order / invoice / PI; a
  converted one links to the bill it became). Business's own number series (initials, e.g. SE/2026-27/1).
- **Bill form:** customer picker + quick add, catalogue item picker (rate / GST / HSN / unit), unit drop-down, calendar
  on every date, customer PO no./date, e-way bill no., vehicle, transporter, supplier bill no./date (purchase), UTR /
  cheque no. (payments), credit / debit note *against invoice* (items fill in from it), charges (freight / packing /
  labour), TCS / TDS, place of supply, reverse charge, godown, loyalty points, received now + payment mode, time auto,
  **⏸ Hold → Resume** (park a half-made bill on the phone — not in Vyapar; a new bill asks first while one is on hold),
  link earlier / later Payment-In / Out to a bill (in the form or from the saved bill copy), ✨ Fill with AI (photo / PDF of any bill →
  form, incl. PO / e-way / vehicle / UTR), entry warnings (duplicate no., unusual price, credit limit, short stock,
  missing GSTIN / HSN).
- **Bill copy / PDF:** Vyapar-style — Tax Invoice title, Original for recipient, item table + total row, CGST / SGST or
  IGST lines, Received / Balance, amount in words (Indian), HSN tax summary, place of supply, UPI QR + bank, terms,
  signature; 12 print theme colours; **Print** button (Android print to any printer), Save PDF, Share, WhatsApp / SMS,
  Bluetooth receipt print, GST files (e-invoice / e-way JSON). Tap any bill number anywhere → full bill copy.
- **Purchase & Expense:** Purchase bill, Payment-Out, Expenses (categories incl. Interest paid / Income tax), Other
  income, Purchase order, Purchase return / Debit note, Purchase FA.
- **Items & Stock:** Catalogue (MRP, wholesale, 2nd unit, barcode, service period, **product photo** camera / gallery),
  Stock, Godowns (move stock), Manufacturing (raw-material list, make, undo), Reorder stock, Items & units, Excel import,
  **own barcode scanner** (CameraX + ML Kit, no Google screen), online catalogue link (customers order → Sale order).
- **Cash, Bank & Accounts:** Cash & Bank (+ account, entries, cheques, reconciliation), Loan accounts (interest), Fixed
  assets (depreciation), Journal entry, Financial year (close).
- **Reports & GST:** 32 reports (P&L, Balance sheet, Trial balance, Cash flow, Day book, Party statement, outstanding,
  item / party P&L, GSTR-1 / 2 / 3B / 9, HSN …) with AI insights · Receivables (age buckets, tap customer → their bills,
  tap bill → copy) · Overdue · Profitability · Daily activity · Customer dashboard · **Targets** (yearly + monthly, year %
  vs month %) · Export to Tally (XML / CSV) · **EBITDA** (Home card + breakdown).
- **Sales team & Service:** Commission (bill, agent, %, TDS 194H, net, paid; customer's usual agent → bills show Due;
  clean copy + Edit / Delete) · Reminders (payment + service, SMS / WhatsApp, AI reminder text) · Samples (party +
  several items, status) · Dispatch (bill / challan no., e-way, courier, In transit / Delivered, Edit / Delete) ·
  Complaints (status, Edit / Delete) · Fabric preview · Vendor compare · Orders / PO.
- **Staff & Attendance:** Team duty, Staff permissions (per area view / edit / none, owner-only areas, backup switch),
  Audit trail.
- **Settings:** Company profile, bill / GST / print settings, print theme, app theme, recycle bin, updates, import old
  data (old-software backups), wipe data (owner, with safety snapshot), backups (locked `.kunwar` file, server backups,
  restore into a new firm / replace), multiple firms, government & approvals (e-invoice IRN, e-way bill, GSTIN check,
  SMS / DLT, payment links — built, switch on after the owner's approvals), app lock (PIN + fingerprint), T&P list.
- **Home:** greeting, NEXORA Guard (AI finds mistakes), overview cards that open their screens (quotations, orders,
  dispatch due, outstanding, collections today, month sales vs target, gross profit %, cash available, EBITDA), Today
  card, monthly target.
- **Ask Nexora (business):** books tool — receivables / payables / unpaid bills / cash-bank / P&L incl. EBITDA /
  balance sheet / day book / party statement + party sales for a period / GST / items / reminders / service / targets /
  online orders / dispatch / samples / complaints / commission / notes; "no such party" + closest names; adds notes
  (user confirms); business chips + greeting; Hinglish; voice (natural voice, conversation mode).
- **Phone app basics:** offline cache + offline bills, firm switcher, app lock, T&P badge, Info (ⓘ) guide on every
  screen, date format from settings, drop-downs tinted, selected chips visible, forms never jump when a drop-down opens
  (fixed at the source: `MenuScrollStop` on every ExposedDropdownMenuBox — see `Nexora20/docs/SCROLL_DRIFT_NOTES.md`),
  camera buttons ask for permission first.

## 5. Planned / open

**Next session (from the owner's list):**
1. Full T&P re-audit: every button on every Business UI screen, then Executive UI; marks only on full pass.
2. Study Vyapar on the owner's PC screen by screen → field checklist; compare every NEXORA screen to it.
3. Barcode scanner: read a real barcode end to end.
4. Live tries (cost AI, ask first): bill reader with PO / e-way / UTR, Ask Nexora writing a note.
5. Fold phone layouts (two panes list | bill / ledger, invoice form + live preview, full item table, 3–4 column Home,
   Flex mode) — owner to choose first.
6. 3D / animation ideas (catalogue 3D / AR, target ring + confetti, Ask Nexora robot reactions, card tilt) — owner to
   choose.
7. Product photo AI (read name / MRP from the photo).

**Bigger roadmap:**
- NEXORA remote support (Get help code, live view, remote operate with roles + consent, audit) — plan in
  `docs/HOP_VYAPAR_PARITY_PROGRESS.md` "NEXT BATCH".
- Guard: daily server run + push notification for new urgent issues.
- Web workspace parity for the newest phone features (Notes, Hold bill, Commission screen, Targets switch …).
- NEXORA website (sub-domain) with policy pages → Razorpay partner.

**Waiting on the owner (decisions / approvals):** GSP account (e-invoice / e-way / GSTIN API), DLT registration + SMS
provider, payment gateway KYC (Razorpay / Cashfree), off-server backups (S3 account), a remote for the phone code repo,
Executive UI target fiscal years.

## 6. How to work here

- Server tests: `python -m pytest tests -q` (full run ≈ 40 min; 1089 passed on 19-11). Write tests with a made-up
  company (`tests/test_hop_reports.py::co` fixture).
- Phone build: `./gradlew.bat :app:assembleDebug` (JAVA_HOME = Android Studio jbr), install with `adb install -r`.
- Deploy: back up the DB first, then pull + restart on the server (`deploy/README.md`); smoke test = a 401 on the new
  route means it exists.
- Update `docs/NEXORA_APP_MAP.md` when adding a module, the help text (`app/hop_help*.py`) for every screen change,
  and the AI checklist.

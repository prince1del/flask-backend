# NEXORA — App Map

One page to find your way around NEXORA: what each module does, where its data
lives, which server route it uses, and which phone / web screen shows it.
Read this first before changing a feature. Keep it current when you add one.

_Last full survey: 2026-11-12 (574 server routes, 54 phone screens)._

---

## 1. The big picture

```
 Phone app (Android, Kotlin/Compose)  ─┐
 Web workspace (browser, app.js)       ├──►  Flask API  ──►  SQLite workspace DB  (business data)
 Desktop app (pywebview shell of web) ─┘   (one server)  └─►  PostgreSQL           (older platform tables)
                                                │
                                                ├──► Google Drive  (document copies, Excel backups)
                                                └──► Gmail (read-only)  (CI mails, SO payment mails)
```

- **One backend for every client.** Phone, web and desktop call the same
  `/api/v1/...` routes and see the same data. Never keep business data only on
  the phone. (Rule: `AGENTS.md`, `.cursor/rules/global-single-backend.mdc`.)
- **Production:** `https://app.houseofprizm.com` (AWS Lightsail, Mumbai).
  Setup, services, backups and deploy steps: `deploy/README.md`.
- **Phone base URL:** `RetrofitClient.BASE_URL` in the Android project.

### Where the code is

| Part | Location |
|------|----------|
| Flask app factory, blueprint registration | `app/web_app.py` (→ `wsgi.py`, `start.sh`) |
| Routes (HTTP endpoints) | `app/routes/*.py`, plus root `filled_orders_routes.py`, `article_master_routes.py`, `nexora_ask_routes.py` |
| Business logic | `app/services/*.py`, root `filled_orders_*.py`, `article_master_*.py`, `order_item_keys.py` |
| Main DB layer (SQLite) | `centralized_db_system/db.py` → class `CentralizedDB` |
| SQLAlchemy models (Postgres) | `app/models.py`, `app/db.py`, `app/db_url.py` |
| Google Drive / Gmail OAuth | `app/storage/` (`oauth.py`, `gmail_oauth.py`, `nexora_docs.py`, `nexora_drive_paths.py`) |
| Web workspace UI | `app/templates/index.html`, `app/static/app.js` (BD), `app/static/hop_app.js` (House of Prizm) |
| Desktop app | `desktop_app.py` (pywebview window over the local Flask server), `build_desktop.bat`, `installer.iss` |
| Phone app | separate repo `AndroidStudioProjects/Nexora20` (package `com.princeoberoi.nexora20`) |
| Server deploy | `deploy/` (systemd units, nginx, backups, APK publish) |
| Tests | `tests/` (pytest) |

---

## 2. Login, roles and workspaces

- **Login:** `POST /api/v1/auth/login` → JWT access + refresh tokens
  (`app/jwt_service.py`). Every API call sends `Authorization: Bearer <token>`;
  `require_jwt_auth` (in `app/routes/auth.py`) checks it. Refresh:
  `POST /api/v1/auth/refresh`. Users live in the SQLite `users` table.
- **Workspace = data silo.** Every business row carries `workspace_id`, taken
  from the logged-in user — never from the client (`app/workspace_tenancy.py`).
  `sales_executive`, `distributor` and `retailer` logins get a private
  workspace `exec_<username>` unless an admin shares one.
- **Roles** (`_ALLOWED_WORKSPACE_ROLES` in `auth.py`): `sales_executive`,
  `distributor`, `retailer`, `unassigned`, `hop_admin`; plus `admin`, and the
  `is_workspace_owner` flag for the workspace's owner (super admin of that
  workspace). Admin screens: `/api/v1/admin/*`, `/api/v1/workspace/users`.
- **Profile / theme / recovery:** `/api/v1/me/*`, `/api/v1/auth/*` (recovery
  PIN, secret question, password reset).

---

## 3. Modules (BD — Bombay Dyeing distribution)

Each block: what it is → main routes → main code → phone screen → web.

### 3.1 My Day (home)
Daily overview: greeting, Target vs Achievement card, total value of
distributor orders by season (Bed/Bath/TOB), Distributors Payment Status card,
global search box.
- Routes: `GET /api/v1/executive/home`, `/api/v1/target-achievement/fy-overview`,
  `/api/v1/filled-orders/season-overview`, `GET /api/v1/search`.
- Phone: `DashboardScreen.kt` (shell + drawer), `BdMyDayPane.kt`;
  search results in `BdArticlesPane.kt` → `GlobalSearchResultsList`
  (order: Article Master, Distributors, Retailers, SO/CI, Stock).
- Search index: SQLite FTS table `global_search_index`.

### 3.2 Customers (Distributors & Retailers master)
- Routes: `/api/v1/masters/distributors*`, `/api/v1/masters/retailers*`
  (CRUD, bulk upload, export); Party 360: `GET /api/v1/executive/party/<type>/<id>`;
  visits: `/api/v1/executive/visits`; duplicate finder: `/api/v1/party-matching/*`.
- Tables: `master_distributors`, `master_retailers` (+ older `distributors`, `retailers`).
- Phone: `BdPartyPane.kt`, `VisitingCardScanner.kt` (`POST /api/v1/utils/scan-visiting-card`).

### 3.3 Article Master (products, MRP, price history)
- Routes: `/api/v1/article-master/*` — list, upload (Excel), manual add, edit,
  price history / seasons, duplicates & merge, brand aliases, download.
- Code: `article_master_routes.py`, `article_master_db.py`, `article_master_parser.py`.
- Tables: `article_master_v2`, `article_price_history`, `article_season_prices`,
  `brand_aliases`, `category_master`.
- Phone: `BdArticlesPane.kt`, `ArticleMasterUploadSheet.kt`, `ArticleMasterManualEntrySheet.kt`.

### 3.4 Order Desk (Filled Order ↔ SO ↔ CI)  — the core workflow
```
Filled Order (distributor's order sheet, Excel)
      │ upload            POST /api/v1/filled-orders/upload  (preview → confirm qty column)
      ▼
SO pack (Bombay Dyeing Sales Order PDFs, or ZIP/RAR)
      │ analyze           POST /api/v1/order-fulfillment/so-pack/analyze(-stream)
      │ match to order    POST /api/v1/order-fulfillment/so-pack/match-filled-order
      ▼
Match run (which SO lines cover which order lines)   table fo_so_match_runs / fo_so_match_so_index
      │
CI (Commercial Invoice PDF = the bill)
      │ upload            POST /api/v1/order-fulfillment/upload/invoice | invoices-bulk(-stream)
      │ link to SO        POST /api/v1/order-fulfillment/confirm-ci-link | confirm-ci-only
      ▼
Pending SO = SO value not yet billed by any CI   GET /api/v1/order-fulfillment/pending-so
```
- Code: `app/routes/data.py` (the big one: SO/CI parsing, upload, match),
  `app/services/fo_so_match_db.py` (match runs, **Pending SO**),
  `fo_so_match_lab.py`, `so_pack_consolidate.py`, `so_article_master_match.py`,
  `filled_orders_*.py`, `order_desk_archive.py` (recycle bin).
- Tables: `filled_orders`, `filled_order_items`, `filled_order_so_link`,
  `fo_so_match_runs`, `fo_so_match_so_index`, `order_lifecycle_tracking`,
  `order_fulfillment_items`, `order_desk_archive`.
- Files: originals are saved under `instance/` and copied to Drive as
  `NEXORA/{Sales Orders|Filled Orders|Commercial Invoices}/{Season}/{Category}/{Distributor}/`
  (`app/storage/nexora_drive_paths.py`). The server copy is kept after the Drive copy
  is made (set `NEXORA_DROP_LOCAL_AFTER_DRIVE=1` to delete it, as on old Render).
  "Show CI/SO" serves Drive first, then the server copy.
- Phone: `BdOrderDeskPane.kt`, `OrderDeskTree.kt` (season → category → distributor
  tree; Pending SO tab built from the server list), `SoPackBusyOverlay.kt`,
  `util/SoPackUploadPrep.kt`.
- Web: Order Desk rail in `index.html` (`#order-fulfillment-workspace`, `showOfSection` in
  `app.js`); tab 7 **Pending SO** is in `app/static/order_desk_money.js`.
- Matching rules: `docs/ORDER_BUNDLE_MATCHING_RULES.md`; recycle: `docs/ORDER_DESK_RECYCLE.md`.
- Speed: `GET /api/v1/order-fulfillment/tracking/<id>` re-checks a CI PDF only once per
  file (`repair_checked` mark in `commercial_invoice_parsed`) and does not re-seed Order
  Match SO lines that are already saved. Keep heavy PDF work out of GET routes.

### 3.4a Seasons (AW26, SS27 …)
- Rule: the season is chosen once, on the Filled Order upload (stored as one spelling,
  `canonical_season`: "aw 26" → AW26). SOs inherit it through the FO match, CIs through
  their SO number, payments through the distributor's season. Bombay Dyeing seasons have
  no fixed dates, so nothing is guessed from a calendar.
- CI whose SO never met an FO: no season. `GET /api/v1/seasons` lists them with a
  suggestion (season/category of the nearest SO number, same distributor first);
  `POST /api/v1/seasons/assign` stores the choice in `so_season_overrides`, and Hisaab
  then places that CI in its season.
- `GET /api/v1/seasons` also gives every season's counts and money and the active one;
  Hisaab takes `?season=` and recomputes totals for it.
- Phone: `ui/screens/SeasonBar.kt` — one app-wide switch (All / AW26 / …) on Order Desk
  and Hisaab, plus the "N CI without season" fix; Order Desk, Pending SO, Hisaab and
  Payment Status follow it (`SeasonSelection`). Web: season picker on the Pending SO and
  Hisaab tabs (`order_desk_money.js`); the "without season" fix is phone-only for now.
- Code: `app/services/seasons.py`, `app/routes/seasons.py`.

### 3.5 Distributor Zone
Menu group with four sections:
- **Distributors Payment Status** — money received per distributor / season /
  category, CD % and TDS settings, due date.
  Routes: `GET /api/v1/distributor-payments/status`, `POST .../deposits`,
  `PUT|DELETE .../deposits/<id>` (Edit / Delete a deposit), `POST .../cd`, `POST .../tds`. Tables: `distributor_category_payments`,
  `distributor_cd_rates`, `distributor_tds_prefs`, `distributor_payment_due_dates`.
  SO-wise collection: `/api/v1/order-fulfillment/payment-collection*`
  (`distributor_payment_entries`; `PUT .../entries/<id>` edits one). After every change an Excel snapshot is
  pushed to Drive `NEXORA/Payment Receiving/` **in a background thread**
  (`app/storage/payment_drive_backup.py`).
  Phone: `BdDistributorPaymentStatusPane.kt`, `PaymentCollectionPane.kt`.
- **Hisaab** (tab inside Payment Status) — order vs received vs billed (CI),
  "received but CI not made", "still to receive", overdue alerts, which CI is
  covered by which payment (oldest payment pays the oldest CI first).
  Route: `GET /api/v1/distributor-ledger`. Code: `app/services/distributor_ledger.py`.
  Phone: `DistributorHisaab.kt`. Web: Order Desk tab 8 (`order_desk_money.js`).
  Each distributor shows a **grand total** (all categories + total to collect).
- **Credit notes** (inside Hisaab) — lower the balance to collect for one
  season + category: `Balance = Order − Received − Credit notes`; invoices unchanged.
  Table `distributor_credit_notes`; files in `/var/data/credit-notes/`.
  Routes: `POST /api/v1/distributor-ledger/credit-notes/read` (PDF/photo → fields via
  Gemini, 300-token cap, counts in the daily AI limit), `POST /credit-notes`,
  `DELETE /credit-notes/<id>`, `GET /credit-notes/<id>/file`.
  Code: `app/services/credit_notes.py`. Phone: `DistributorHisaab.kt` (+ Credit note
  sheet). Web: Hisaab tab, "Credit notes" table and form.
- **Check with party ledger** (inside Hisaab, per distributor) — upload the company
  ledger; lists invoices missing in NEXORA, CIs not in the ledger, amount
  differences, payments missing either side (same amount, ±10 days) and credit notes.
  SAP Excel is read without AI (`statement_of_account.py`); PDF/photo is read by
  Gemini (12k-token cap, daily AI limit). `POST /api/v1/distributor-ledger/reconcile`.
  Code: `app/services/ledger_reconcile.py`. Phone: `LedgerCheckDialog.kt`. Web: Hisaab
  distributor header button.
- **Statement of Account** — `POST /api/v1/statement-of-account/from-ledger`
  (`app/services/statement_of_account.py`). Phone: `BdStatementOfAccountPane.kt`.
- **Secondary Sale** — `/api/v1/distributor-secondary-sales/*`. Phone: `BdSecondarySalePane.kt`.
- Zone overview: `GET /api/v1/executive/distributor-zone`. Phone: `DistributorZonePane.kt`.
- Grievances: `/api/v1/distributor-grievances/*`. Phone: `BdGrievancesPane.kt`.

### 3.6 Mail Sync (Gmail, read-only)
Two readers on the same Gmail connection (`storage_accounts`, provider `gmail`):
1. **CI import** — finds invoice PDFs, keeps only those whose text says
   "Bombay Dyeing", and sends each through the same CI upload path as a manual
   upload. Unsure ones wait in "Needs review".
   Routes: `/api/v1/mail-sync/*` (connect, poll, status, pending, confirm,
   reject, log). Code: `app/services/gmail_ci_sync.py`, `mail_sync_diagnostics.py`.
   Tables: `gmail_pending_imports`, `gmail_import_log`, `gmail_sync_processed_messages`.
2. **SO payment mails** — Bombay Dyeing "RFA … for Cash payment" mails. Reads the
   mail table (SO, pcs, gross, CD, TDS, payable, due date), runs 5 checks
   (mail vs SO PDF, distributor, order sheet qty, CD/TDS vs Payment, already in
   Order Desk), answers common questions itself, then matches the SOs in Order
   Desk and sets CD/TDS/due date. Every save records an Undo.
   Routes: `/api/v1/so-mail/*` (scan, imports, confirm, reject, undo, summary).
   Code: `app/services/so_mail_parser.py`, `so_mail_import.py`, `so_mail_cron.py`.
   Table: `so_mail_imports`. Server timer: `nexora-so-mail.timer` (every 30 min).
- Phone: Settings → Mail sync (`BdSettingsPanes.kt`), `SoMailReview.kt`;
  notifications: `reminders/SoMailNotifyWorker.kt` (polls `/so-mail/summary`).
- Web: Order Desk tab 9 **SO payment mails** (`order_desk_money.js`) — Gmail connect,
  Check SO mails, question cards, Confirm / Ignore / Undo.

### 3.7 Target vs Achievement
Yearly / monthly targets, achievement uploads (sales Excel), category and
distributor breakup, achievement channels, manual "others" lines.
- Routes: `/api/v1/target-achievement/*` (25 routes).
- Code: `app/routes/target_achievement.py`, `app/target_achievement/`,
  `app/services/sales_achievement_parser.py`, `app/fiscal_year.py`.
- Tables: `target_achievement_*`, `target_manual_category_*`, `target_others_lines`.
- Phone: `TargetAchievementPane.kt`.

### 3.8 Market Visit (DSR — daily sales report)
Retailer visit form (stock, placement, competitor brands, issues), day close,
drafts, export, AI-polished notes and narratives, approach-a-new-distributor flow.
- Routes: `/api/v1/dsr-market/*` (33 routes). Code: `app/routes/dsr_market.py`,
  `app/services/dsr_notes_polish.py`, `dsr_visit_narrative.py`.
- Tables: `dsr_market_visits`, `dsr_day_closures`, `dsr_*` lookups,
  `dsr_approach_distributors`.
- Phone: `BdMarketVisitPane.kt`, `RetailerVisitQuestionnaire.kt` (largest screen),
  `DsrReportFolderSheet.kt`, `ApproachDistributorPane.kt`, `util/DsrVisit*.kt`.

### 3.9 PJP (Permanent Journey Plan)
Monthly beat plan: which market / party on which day.
- Routes: `/api/v1/pjp/*` (today, week, months, import + preview).
- Tables: `monthly_pjp_days`, `monthly_pjp_meta`, `weekly_pjp_plans`.
- Phone: `PjpPane.kt`, home widget `widgets/PjpHomeWidget.kt`. Web: `app/static/pjp.js`.

### 3.10 Call List
Upload a calling list, work rows, daily retailer calls with questions and
answers, deals, archive.
- Routes: `/api/v1/call-lists/*` (19 routes). Code: `app/routes/call_lists.py`.
- Phone: `BdCallListPane.kt`, `DailyRetailerCallPane.kt`.

### 3.11 To-Do
Personal to-dos with due dates, reminders (also when the app is closed),
snooze / hold / done.
- Routes: `/api/v1/personal-todos/*`. Table: `personal_todos`.
- Phone: `PersonalTodoPane.kt`, `ReminderSetupDialog.kt`, `reminders/*`
  (WorkManager workers, boot receiver), widget `widgets/TodoHomeWidget.kt`.
  Web: `app/static/personal_todos.js`.

### 3.12 Catalogues & Google Drive
Connect Google Drive, browse / preview / download files, catalogue folders.
- Routes: `/api/v1/storage/*` (connect, account, files, preview, download,
  upload, search, sync), older `/api/gdrive/*`.
- Code: `app/storage/*`, `app/services/gdrive_service.py`.
- Phone: `GdriveCatalogueSheet.kt`, `NexoraPdfViewer.kt`, `NexoraSpreadsheetViewer.kt`.

### 3.13 Ask Nexora (AI assistant)
Natural-language questions over the user's data ("how much CD does X pay").
- Routes: `POST /api/v1/nexora/ask` (+ examples, teach, learned, gemini-status),
  `/api/v1/ai-assistant/query`, troubleshoot log `/api/v1/ask-nexora/troubleshoot`,
  usage `/api/v1/ai/usage-summary`.
- Code: `nexora_ask.py`, `nexora_ask_learn.py`, `nexora_ask_routes.py`,
  `app/services/nexora_ai_agent.py`, `gemini_models.py`.
- Phone: `AskNexoraSheet.kt` (calls `/api/v1/ai-assistant/query`); web calls `/api/v1/nexora/ask`.
- Flow: the rule engine answers first; the Gemini agent takes over when it cannot,
  and for counting/hisaab questions (`_looks_like_ai_data_question` in `data.py`).
- Whole-app reading: tool `read_app(path, query)` (`app/services/nexora_app_reader.py`)
  calls any allow-listed GET route (~65 BD screens) as the asking user — the phone's
  token or the web session's identity — so the AI sees exactly what that user sees.
  `{id}` accepts numbers only; file/export/admin routes are not on the list; replies are
  trimmed (40 items, 400 chars). New module → add one line to `APP_READ_ROUTES`.
- AI tools include `get_distributor_hisaab` (CI count/status per category, received,
  still to collect, SOs not billed) and `get_pending_so`.
- Follow-ups: table `ask_nexora_turns` keeps each user's last turns; inside a 20-minute
  window a short or "uska / aur / bhi" question goes to the agent with that history,
  so it stays on the same distributor/item until a new one is named.
- Gemini: key in `/etc/nexora.env`, project `Default Gemini Project` on the paid
  prepay tier (₹500 credits, auto-reload off — the spend cap). Agent tries
  `gemini-3.5-flash-lite` first (~2-3 s per answer), then `gemini-3.6-flash`, then `GEMINI_FLASH_MODEL`.
  Override with `GEMINI_AGENT_MODELS`. Usage: `ai_agent_usage_log`.
- Learning: after a one-step tool answer, the question's shape and its tools are kept
  in `ask_nexora_learned` (e.g. `{distributor_name} ke kitne so pending hai` →
  `get_pending_so`). A later question of the same shape fetches that data first, so
  the AI writes the answer in one round (≈5 s → ≈2 s). Follow-ups are not learned;
  numbers are always fetched fresh. Usage rows show `nexora_agent_learned`.
- Limits: each user gets 50 AI questions and 100 new voice clips per India day
  (`NEXORA_AI_DAILY_LIMIT`, `NEXORA_VOICE_DAILY_LIMIT`; 0 = off), counted from
  `ai_agent_usage_log`. Past the limit the rule engine answers and only cached voice
  clips play. Every Gemini call carries `maxOutputTokens` so a stuck call stays cheap.
- Voice: `POST /api/v1/ai/voice` {text, voice?} → WAV in a natural Hinglish voice
  (`app/services/nexora_voice.py`, Gemini `gemini-3.8-flash-tts`, disk cache
  `tts_cache/` next to the SQLite file). `GET /api/v1/ai/voices` + `POST .../voices/select`
  keep each user's voice in `ask_nexora_voice_prefs` (default Kore).
  Phone: `ui/screens/AskNexoraVoice.kt` (Settings → Ask Nexora voice; plays answers,
  falls back to the phone's TTS offline). Web chat: not wired yet.

### 3.13a Galaxy Watch (Wear OS)
- Server: `app/routes/watch.py`. `POST /api/v1/watch/pair` (phone login) → a watch key
  (hash stored in `watch_keys`); `DELETE` switches all of the user's keys off. With
  header `X-Watch-Key`: `GET /summary` (active season, to collect, SOs to bill, overdue,
  to-dos due, today's PJP, SO mails waiting) and `POST /ask` (Ask Nexora as that user,
  haan/nahi included). The key opens no other route.
- Phone: `ui/screens/WatchConnect.kt` — Settings → Galaxy Watch → Connect / Disconnect;
  hands the key to the watch over the Wear data layer (path `/nexora/watch`).
- Watch app: Android Studio module `:wear` (same applicationId and signing key as the
  phone — required for the data layer). `NexoraTileService` (tile, refresh 30 min) and
  `WatchMainActivity` (numbers + voice ask). Installed on the watch by ADB over Wi-Fi;
  it is not on the phone OTA.

**Watch app updates (OTA):** `GET /api/v1/watch/app/latest` and `GET /api/v1/watch/app/download`
(X-Watch-Key) serve the newest watch build from `/var/data/app-releases/wear/`
(publish with `bash deploy/publish_watch_apk.sh "notes"` after `:wear:assembleDebug`).
The watch checks on every open and shows "⬆ Update watch app"; the phone's
Settings → Galaxy Watch → **Update watch app** sends `/nexora/watch/update` to the watch.
The user taps Install on the watch (first time: allow installs from this source).

### 3.14 Settings, help, updates
- Company profile `/api/v1/company-profile`; UI theme `/api/v1/me/ui-theme`.
- In-app help: `BdHelp*.kt`, `HindiHelp.kt`, `ui/i18n/`.
- **Phone auto-update (OTA):** `GET /api/v1/app/android/latest` + `/download`
  (`app/routes/app_updates.py`); release files in `<persistent disk>/app-releases/android`;
  publish with `bash deploy/publish_android_apk.sh "notes"`; phone side
  `ui/update/AppUpdate.kt` (`AppUpdateGate`). versionCode = minutes since 2026-01-01.
  The gate checks after login, every 30 min while open and on every return to the app;
  `reminders/AppUpdateNotifyWorker.kt` checks hourly in the background and posts one
  notification per new version with its notes. Write notes as short points separated by
  ";" — the update window and Settings → Updates show them as bullets.
  `GET /api/v1/app/android/releases` serves `history.json` (every build's notes; the
  publish script appends to it) for Settings → Updates.

---

## 4. House of Prizm (HoP) — separate business module
Interior / furnishing trade: customers, vendors, leads, deals board, quotations,
invoices, payments, dispatches, samples, rate sheets and rate compare, commission,
projects, Vyapar import, document PDFs.
- Routes: `/api/v1/hop/*` (79 routes). Code: `app/routes/hop.py`, `app/hop_*.py`,
  `app/services/vyapar_*.py`, `fabric_preview.py`, `gstin_lookup.py`.
- Tables: `hop_*`. Role: `hop_admin`.
- Phone: `Hop*.kt` screens. Web: `app/static/hop_app.js`, `hop_commercial_web.js`.
- Does not touch BD tables.

## 5. Older / platform layer (mostly not used by the BD screens)
SQLAlchemy models in PostgreSQL: `sales_orders`, `invoices`, `finance_*`,
`inventory`, `workflows`, `events`, `business_rules`, `conversations`,
`knowledge_graph_*`. Routes: `/api/v1/sales-orders`, `/api/v1/invoices`,
`/api/v1/finance/*`, `/api/inventory/*`, `/api/business/*`, `/api/workflows/*`,
`/api/events/*`, `/api/intelligence/*`, `/api/v1/phase2_8/*`, `/api/v1/reports/*`,
`/api/v1/operations/*`, `/api/v1/fulfillment/*`. Check usage before building on these.

---

## 6. Server jobs and backups
| What | When | Where |
|------|------|-------|
| `nexora` web service (gunicorn gthread, 2 workers × 6 threads) | always | `deploy/nexora.service`, `start.sh` |
| SO payment mail scan | every 30 min | `deploy/nexora-so-mail.timer` → `python -m app.services.so_mail_cron` |
| Server backup (Postgres + SQLite + `instance/` files, 14 days) | 02:30 IST daily | `deploy/backup_daily.sh` |
| Laptop backup pull (60 days + 1st of month kept) | 10:00 daily | `deploy/laptop_backup.ps1` (Windows Task Scheduler) |
| Payment Excel → Drive | after each payment change (background) | `app/storage/payment_drive_backup.py` |

## 7. Phone ↔ web parity (2026-11-12)
On web since 2026-11-12: Pending SO, Distributor Hisaab, SO payment mails
(Order Desk tabs 7–9).
Phone-only today (no web screen yet): Distributor Payment Status (CD/TDS
settings), Mail sync CI review, Call List, Statement of Account, Secondary
Sale, Grievances. Market Visit DSR on web is partial (visits, competitor brands,
export only). When adding to web,
reuse the same routes listed above — no new endpoints needed.

## 8. Rules of the road
- New feature = Flask route first, then phone **and** web.
- Data changes go through the app's routes, not one-off scripts on the live DB.
- UI text is English.
- The GitHub repo is public: no real business data, passwords or keys in code,
  tests or docs.
- Before you change a module, find it here, then read its route + service file.

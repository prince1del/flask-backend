# NEXORA — read this first (short on purpose)

Full feature detail (every UI, screen, setting, what was built when): **`docs/NEXORA_FEATURES.md`** — read only the
part you need. Other maps: `docs/NEXORA_APP_MAP.md` (module → route → table → screen), `AGENTS.md` (single backend +
AI rules), `deploy/README.md` (server, backups, deploy). This repo is **public**: never put server addresses, keys,
passwords, PINs or real business data in it.

_Last updated: 2026-11-21. Keep this file short: rules + what is pending. Finished work → `docs/NEXORA_FEATURES.md`._

## 1. What NEXORA is

One app, one login, five UIs on **one backend and one set of books**: Admin Zone, Executive (Bombay Dyeing field
team), Business (Vyapar-style billing + accounts), Distributor (the distributor's own firm), Retailer (preview only).
Mobile first, premium navy + gold, calm screens, Hinglish-friendly, ⓘ guide on every screen. Every UI shows the
subscriber's own brand (never "House of Prizm" for another business).

## 2. Owner's rules (always)

- **One backend.** New feature = server route → phone (and web later). Phone, web, desktop read the same `/api/v1`.
- **Mobile first.** Build + check on the phone (`adb install -r`, no OTA).
- **AI in every feature**, but AI never saves by itself (user confirms); one capped call, inside the daily limit,
  logged. Ask before any test that costs more than ₹10.
- **UI text in English** (chat can be Hinglish).
- **Separate data for every UI** — "not a single byte of one UI in another". Every phone call sends `X-Nexora-UI` +
  `X-Hop-Firm`; the server picks that UI's own firm (`hop_firms.active_ws`). Distributor = workspace `<base>__dist`.
- **Never write to the live DB from a script** (no UPDATE/DELETE/INSERT, not even clean-ups). Fix the code; the owner
  changes data in the app. Read-only checks only.
- **Commit + push to `main` after every fix** (this repo). Phone repo: local commits only.
- **Ask the owner before every server deploy.**
- **Write the plan here before starting a batch**; ask the owner what to do first; update this file after each change.
- **Notes inbox:** work ONLY on notes for Claude — party "claude" (row shows "👤 claude") or body starting
  "[For Claude]". Reply in the note (By: Claude), then ✓ Mark as done — through the app only.
- **Don't touch:** Wipe all data, Import old data on real data, Start day, Financial year close, Android permission
  prompts, the phone's own lock password.
- T&P (Tested & Passed) mark only after every button of a screen was pressed on the latest phone build. Tests use
  made-up companies.

## 3. Where things are

| Part | Where |
|---|---|
| Server | Flask `app/web_app.py`, routes `app/routes/*.py` (books = `app/routes/hop.py`, `/api/v1/hop/*`), logic `app/hop_*.py`, `app/services/*.py` |
| Distributor | `app/hop_distributor.py`, `hop_dist_extra.py`, `hop_dist_terms.py` (companies & price rules), `hop_dist_bulk.py` (bulk SO/CI/CN/DN), `hop_dist_brand.py` (Brand desk), routes `/dist/*` |
| AI | `app/services/hop_ai.py` (readers, insights, books answers), `nexora_ai_agent.py` (Ask Nexora tools, UI walls `tool_allowed`), `nexora_guard.py` |
| Web | `app/templates/index.html`, `app/static/app.js`, `hop_app.js` |
| Phone | `C:\Users\princ\AndroidStudioProjects\Nexora20`, package `com.princeoberoi.nexora20`; screens `ui/hop/*.kt`, `ui/screens/*.kt`; spacing `NxSpace` (`ui/theme/Theme.kt`) |
| Production | AWS Lightsail Mumbai, systemd `nexora`; deploy = `sudo -u nexora git -C /opt/nexora pull` + `sudo systemctl restart nexora` |

- Every screen change: help text in `app/hop_help.py` + `app/hop_help_screens.py` (each "On this screen" line has " — ").
- Server tests: `python -m pytest tests -q` (full ≈ 40 min; use `-k dist` / `-k help` etc. for the part you touched).
- Phone build: `./gradlew.bat :app:assembleDebug` (JAVA_HOME = Android Studio jbr); long builds in the background.
- Kotlin: never write `/*` inside a comment.
- Phone on Wi-Fi debug: the port changes — `adb mdns services`, else ask the owner for IP:port. Phone driver scripts
  live in the session scratchpad (`nx.py`, `reply_notes.py`, `list_notes.py`, `pin.sh`); the app-lock PIN is in
  Claude's private memory only.
- Cursor also works on this repo (own SSH key `nexora_server_cursor`, rules in `.cursor/rules/live-server-safety.mdc`
  + `AGENTS.md`). Review its commits; it does not do calm-screen work.
- `/etc/nexora.env` is sourced by the backup script: quote every value with spaces or `< >`.

## 4. Pending — do these next

**Phone first (needs the phone on Wi-Fi debug):** `adb install -r` the latest APK (Goods received, notes ✓✓,
GSTIN auto fetch are deployed but the phone has the older build); then check on the phone: Terms book, + Add company,
Goods received, notes ✓✓ / Raise again / Open ▸ link / #numbers, Brand desk, GST by category, "kunwar home" only in
Distributor. Notes done 21-11: D1, D2, D4, D5, D7. Detail of all of it: `docs/NEXORA_FEATURES.md` §10.

**Batch now (owner 21-11: "bina phone ke … pehle yeh sab banao"), in this order:**
1. (D9) Warehouse item card: tap an item → details, godown stock, last stock entries, + Add / − Reduce stock (asks
   the company), Edit item, Order this; server `/dist/items/<id>`.
2. Company Edit details (name, GSTIN, address, state, contact) from Brand desk / Terms book.
3. Manual stock in the Distributor UI asks the company (or "Opening stock") → journal line with `vendor_id`.
4. CD + incentive per company / order: expected CD % and incentive, company credit note matched to it, received vs
   still due; counted in margin.
5. Per company: ordered → CI → received → pending (qty + ₹) and margin by company / category (report + AI + Ask Nexora).
6. ✨ AI reads a company price sheet → per article MRP / PTR / retailer % / mark-up → Terms book suggestion (user saves).
7. (B3) Cash & Bank "Add new account" not visible — find + fix.
Then: tests, APK build, commit + push, ask the owner before deploy. Reply #26 (multi-brand) after the phone check.

**Notes still open:** (D6) GSTIN Fetch in every UI + (D8) barcode scan → item or Add new — built by Cursor, check +
reply; #26 multi-brand margins — reply after the phone check. Business: (B1) status bar time / signal invisible with
the theme; (B3) in the batch above; (B4) payroll — reply with a plan; (B5) partners / roles — waits for the owner's 3
answers (plan in `docs/PARTNERS_AND_ROLES_PLAN.md`). Untagged, leave alone: "yaha pe add new ka option bhi do…",
"QA test note rates bhejne hai".

**Built but not tried with real data / on the phone:**
- Bulk import (SO/CI/CN/DN) with a real company file; RAR on the server.
- Companies & terms + price rule with a real company; Brand desk; shipments.
- Salary with a real staff member and payment.
- Article Master with the 3 real AW26 sheets (owner reads + saves); clear old "Sheet Sets" items in Business Catalogue.
- Real barcode read, real print, AI bill reader with PO / e-way / UTR, Ask Nexora writing a note, link payments.
- Supplier bill copy upload with a real bill.

**Calm the last busy screens (Claude only):** Executive My Day + Order Desk; Distributor Orders → SO → CI and
Company ledger; bill form and the remaining screens → `NxSpace`, title + count, soft cards, no ".00".

**Waiting for the owner's yes / choice:**
- "Keep users interested": smart Today list on Home, morning brief, weekly health score, setup checklist, AI wins.
- ✨ AI rate watch (PF / ESI / tax change alert, never auto-applied).
- Retailer UI first version; web parity (Distributor UI, Salary, Article Master, Forecast by retailer, newer phone
  features).
- Print many bills next steps (email to CA / Ask Nexora / cover page); Login screen in the navy-gold style.
- Owner's list: full T&P re-audit, study Vyapar → field checklist, fold-phone layouts, 3D / animation ideas, product
  photo AI.

**Waiting on outside accounts:** GSP (e-invoice / e-way / GSTIN API), DLT + SMS provider, payment gateway KYC,
off-server backups (S3), a remote for the phone repo, Executive target fiscal years.

**Bigger roadmap:** NEXORA remote support, Guard daily server run + push notifications, web parity, NEXORA website
with policy pages → Razorpay partner.

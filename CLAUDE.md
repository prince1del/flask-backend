# NEXORA — read this first (short on purpose)

Full feature detail (every UI, screen, setting, what was built when): **`docs/NEXORA_FEATURES.md`** — read only the
part you need. Other maps: `docs/NEXORA_APP_MAP.md` (module → route → table → screen), `AGENTS.md` (single backend +
AI rules), `deploy/README.md` (server, backups, deploy). This repo is **public**: never put server addresses, keys,
passwords, PINs or real business data in it.

_Last updated: 2026-11-20 (night). Keep this file short: rules + what is pending. Finished work → `docs/NEXORA_FEATURES.md`._

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

**Deployed 20-11 night (`83ba366`):** GST by category, firm move between UIs, Brand desk — not yet opened on the phone.
Owner moved "kunwar home" to the Distributor UI (20-11 night) — check it shows only there, with its bills/parties/notes.

**Open notes — owner's order D1 → D8 (D1–D5 done in code, reply + mark done after phone check):**
- (D1) done in code — firm "kunwar home" showed in Business; move option + per-UI firm name on the phone.
- (D2) done in code — type-ahead party search in the bill form (`ui/util/TypeAhead.kt`), items match every word.
- (D3) done in code — Brand desk (per company setup, orders/SO/CI, goods still to come, shipments, money).
- (D4) done in code — N opens the full note form (party, remind, photos, "For Claude"); no N on the Notes page.
- (D5) done + replied + marked done 20-11. Also new: 📸 Take screenshot (screen behind, draw in red) in the N note form.
- Notes (21-11, built, phone installed, server deployed `8df3aca`): ↩ Raise again (reason; a user reply in a done note also
  reopens), done / raise history lines, "For: Claude", done → pick a screen → "Open ▸" link, N + For Claude founder only
  (server 403 for others). When Claude marks a note done: pick the screen where the fix shows.
- (D9) **new note:** "[Distributor UI] isme aster ya kisi or item pe click kyu nahi ho raha" — read it (may have a photo).
- (D10) **owner asked:** Brand desk has no "+ Add company"; company details live in Parties (supplier) + Companies & terms — add one place.
- (D6) **to check + reply:** GSTIN Fetch in every UI (built by Cursor).
- (D7) **to reply:** "GST kyu nahi dikha raha" → GST by category screen (Article Master → GST by category).
- (D8) **to check + reply:** barcode scan → item or Add new, manual stock (built by Cursor).
- Business notes: (B1) status bar time / signal invisible with the theme; (B2) done with D4; (B3) "screen boring,
  Add new account not visible" (likely Cash & Bank — check); (B4) INDPayroll-like payroll — reply with a plan (needs +
  how to test with data); (B5) partners / team roles — waits for the owner's 3 answers (is a partner equal to the
  owner? shared notes for partners or whole team? which roles first?), plan in `docs/PARTNERS_AND_ROLES_PLAN.md`.
- Untagged, leave alone: "yaha pe add new ka option bhi do agar user ki profile kuch or huyi to" (check the tag
  first), "QA test note rates bhejne hai".

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

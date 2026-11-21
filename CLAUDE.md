# NEXORA — read this first (short on purpose)

Full feature detail (every UI, screen, setting, what was built when): **`docs/NEXORA_FEATURES.md`** — read only the
part you need. Other maps: `docs/NEXORA_APP_MAP.md` (module → route → table → screen), `AGENTS.md` (single backend +
AI rules), `deploy/README.md` (server, backups, deploy). This repo is **public**: never put server addresses, keys,
passwords, PINs or real business data in it.

_Last updated: 2026-11-21 (evening). Keep this file short: rules + what is pending. Finished work → `docs/NEXORA_FEATURES.md`._

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
- **Auto deploy after every server fix** (owner 21-11: "auto deploy karo har baar") — push, pull on the server,
  restart, smoke-check the new route (401 = there); no need to ask.
- **OTA app update only when the owner asks** (owner 21-11: "jab mangu tab do") — build + `adb install` is fine,
  `deploy/publish_android_apk.sh` only on the owner's word.
- **Write the plan here before starting a batch**; ask the owner what to do first; update this file after each change.
- **Notes inbox:** work ONLY on notes for Claude — party "claude" (row shows "👤 claude") or body starting
  "[For Claude]". Reply in the note (By: Claude), then ✓ Mark as done — through the app only.
  **"Phone ke notes check karo" = only the notes tagged claude** (owner 21-11), in the UI the owner names (e.g.
  Distributor UI); untagged notes are not read out, listed or worked on.
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
- Phones: always `adb -s <serial or ip:port>` and check `ro.serialno` first — never a phone the owner did not name.
  Old phone Fold (USB); new phone Vivo V2144 (Wi-Fi, paired 21-11; ask the owner for "IP address & Port"; pairing port
  ≠ connect port — `adb mdns services`). Phone driver scripts (`nx.py`, `list_notes.py`, `mark_done.py`, `typein.py`)
  read `ANDROID_SERIAL`; they press Back, which on the Vivo leaves the app — drive notes on the Fold, or take
  screenshots step by step. Fold screenshots: `screencap -d <display id>` (two displays). The app-lock PIN is in
  Claude's private memory only.
- Test with a NEW sign-up of each UI, not only the founder login (the founder skips role checks) —
  `tests/test_self_serve_distributor_books.py` does Business / Distributor / Retailer / Executive.
- Cursor also works on this repo (own SSH key `nexora_server_cursor`, rules in `.cursor/rules/live-server-safety.mdc`
  + `AGENTS.md`). Review its commits; it does not do calm-screen work.
- `/etc/nexora.env` is sourced by the backup script: quote every value with spaces or `< >`.

## 4. Pending — do these next

**Phone check (latest APK installed 21-11 morning):** check on the phone: Terms book, + Add company,
Goods received, notes ✓✓ / Raise again / Open ▸ link / #numbers, Brand desk, GST by category, "kunwar home" only in
Distributor. Notes done 21-11: D1, D2, D4, D5, D7. Detail of all of it: `docs/NEXORA_FEATURES.md` §10.

**Built 21-11 (night), server deployed `d2a7b18` + phone installed (21-11 morning), not checked on the phone yet:**
(D9) Warehouse item card + hand stock that asks the company; company Edit details (GSTIN auto fetch); Company report
(menu Company → Company report: ordered → CI → received → to come, margin by category, CD earned / lost / pay soon,
incentives + company credit note link); Terms book "✨ Margins from the company sheet"; (B3) Cash & Bank "+ Add
account" in plain sight. Server: `app/hop_dist_company.py`, `/dist/items/*`, `/dist/companies/<id>/details|report|incentives`,
`/dist/terms-book/<id>/from-sheet`; AI `company-report`, Ask Nexora `dist_company_report`, `dist_item_card`.
Next after deploy + install: check all on the phone, reply D9 / B3 / #26 notes.

**App lock is OFF on the owner's phone (owner 21-11: "jab main kahu lock on tab karna") — never turn it on by
yourself.** It now has its own menu item in every UI: Settings → App lock (PIN).

**Owner said "weird, fix later" (21-11):** Warehouse item card (`DistItemCardDialog`, `HopDistCompanyScreens.kt`) —
big 2-line title, info scattered, buttons uneven, empty space; redo as calm cards (price / stock / actions / history).

**Deployed 21-11 `a9e006f`:** company PIN code, more contacts, designation, Guard tip → Brand desk + Add company.

**Built 21-11 (phone installed, server deployed `c5529b7`; offline not tried — owner to test, then reply #45/#46):** lists already work offline (HopOfflineCache) and bills queue
(HopOffline). Gaps → (1) offline bill number: each login has its own series `<prefix>/<fy>/<code>-<n>` (server
`/party-transactions/offline-series`, phone keeps the next number, the main series never clashes); (2) notes (with
photos) queue offline and send themselves; (3) the queue card says "waiting to send" for notes too.

**Now (21-11):** in-app note numbers (Distributor, Kunwar home main) — #24 Retailer UI notes (built: server
`hop_firms` UI "retailer" → `<base>__retail` pushed `a84d396`, phone `badcabd` — deployed
+ installed 21-11, owner checks on the phone),
#25 / #26 / #27 = QnA (fast entry without barcode, product photo → EAN / MRP / GST, voice billing) — answer with a plan.
Done 21-11: #28 offline bill numbers, #29 offline notes (owner tests later), #30 claude party fix; D6 GSTIN auto fill
everywhere, B1 status bar icons, item card calm redesign (phone installed). B4 payroll parked (docs/PAYROLL_PLAN.md).
Always say the in-app #number, never the DB id.

**New notes 21-11 (Distributor, Kunwar home):** #45 tagged claude — offline: a new bill gets no number (photo);
#46 party "claudes" — notes do not save offline in any UI; untagged (tagging was broken, fixed 21-11 — ask the owner):
#41 notes for the Retailer UI, #42 QnA barcode-less fast product entry, #43 product photo → EAN / MRP / design / GST,
#44 voice billing without mistakes. Firm "kunwar home" (moved, `__f2`) holds: B1 status bar, B4 payroll, #17
"add new option if the profile is different" (login chips?). Done 21-11: B3 (it was the LOGIN screen → navy-gold +
Create account button), #26 multi-brand (Distributor main). D9 replied, kept open (item card redesign).

**Done 21-11 evening (OTA 2.0.467225 published + Fold installed, owner checks):** Edit profile — KH logo letters,
GSTIN Fetch, type / category search + Add, IFSC → bank + branch address (every UI), signature pad buttons. Detail §10.

**Distributor UI — claude notes (21-11 ~12:40):** replied + ✓ done in the app: #2, #10, #16, #17, #19, #24-#37.
**Done 21-11 evening (APK 15:57 on BOTH phones; server `9248c8b`):** #9 bulk delete (photo = Parties list) on every
bill list + Parties; Distributor bottom Orders → Orders/SO/CI; #12 GST % on Article Master rows; #22 Warehouse row
opens the item card (checked in code); #23 Article Master calm (two button rows, Select visible, small AI card).
Distributor notes #2-#37 all replied + ✓ done. **New sign-up fix:** a Distributor / Retailer sign-up got
"Insufficient permissions" (role `distributor` / `retailer`, books need `hop_admin`) → `auth._self_serve_books_user`
lets a self-signed-up business into its OWN workspace's books only.
**Next:** (1) version check phone side — AppUpdate.kt: own VERSION_CODE < `min_version_code` → no "Later" (server
done: latest.json `min_version_code` / env `NEXORA_MIN_APP_VERSION_CODE`, publish `MANDATORY=1` / `MIN_VERSION_CODE=n`);
(2) new notes #38 (screen name in the top bar, not the firm name), #39 ("where do I type?" — clearer forms);
(3) cursor.md (owner: check, don't trust blindly): "Company invoices" in Orders→SO→CI opens Purchase Bills,
"Ask AI ›" does nothing; (4) smaller price-change banner on Article Master (offered).

**#32-#35 BUILT 21-11 (owner picked first):** numbers never repeat (server refuses a used number), delete frees the
number, Cancel bill keeps it ("Cancelled" in the list), offline bills on top of the bill list with Share / Delete,
offline number shown in the form and never counted backwards (detail §10). Owner tests offline, then reply #32-#35.
**Offline everywhere (owner 21-11):** every change made offline is kept and synced by itself, and shows on the screen
at once with no "waiting" labels (phone interceptor, detail §10); #34 was the delete screen locking offline — fixed.
**Done 21-11 afternoon (server `a5e9ccb` deployed, phone `fa528d6` installed 12:21, owner checks):** #10 many note
photos, #16 scan → New item in the bill form, #17 KH initials in the Hub list + Customer care feedback (ⓘ guide →
Report a problem / Suggest a change; Menu → Help & feedback; Customer care inbox on the platform owner's login; no
floating button), #37 EAN field + EAN column in price lists, #2 GSTIN fetch checked (already everywhere), photo
rotation fix. **Next:** #9 / #12 / #22 / #23 — need the note photos (open each note on the phone first), then reply to
all claude notes together (owner: "jawab saare baad main ek saath").

**QnA #25/#26/#27 (Distributor UI):** step 1 "Scan many" BUILT + phone installed 21-11 (owner checks) — server
`ccf16d3` (scan-index / scan-check) deployed 21-11 `95600f7`; phone bill form "Scan many" + Article Master
Scan → counted vs books (detail `docs/NEXORA_FEATURES.md` §10). **Step 2 photo → new product BUILT 21-11** — server `91f8d32` deployed; phone Article Master "✨ Photo → new" + "Find by
photo", bill form "Photo" (detail §10). Owner checks on the phone. **Step 3 voice bill BUILT 21-11** (+ unclear speech: sound match, AI picks only from
the saved list, learned picks; same ears in Ask Nexora) — detail §10. All 3 steps: owner checks on the phone, then
reply in the notes (numbers first).

**Notes still open:** (D6) GSTIN Fetch in every UI + (D8) barcode scan → item or Add new — built by Cursor, check +
reply; #26 multi-brand margins — reply after the phone check. Business: (B1) status bar time / signal invisible with
the theme; (B3) built, reply after the phone check; (B4) payroll — plan written in `docs/PAYROLL_PLAN.md` (reply in the note, in Hindi, with the 3 questions); (B5) partners / roles — waits for the owner's 3
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

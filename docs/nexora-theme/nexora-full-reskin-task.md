# NEXORA — full app reskin task (for Cursor)

## Scope change from the earlier task
The earlier task (`nexora-theme-implementation-task.md`) was additive-only — new theme for new screens, existing gold UI untouched. That is now superseded for the *existing* UI: the founder wants the whole app (header, sidebar, buttons, toasts, modals, cloud hub, everything currently gold/amber) moved to the cyan/violet sci-fi palette.

## Strategy — recolor, don't migrate
Do **not** rewrite `premium.css`'s structure or move existing elements onto the new `.nx-card` / `.nx-btn` / `.nx-nav-item` classes. That would touch working, wired functionality (fixed-position layout, toast system, confirm modals, cloud hub) for no real gain. Instead: change the *color values* inside `premium.css` itself, keep every selector, class name, and layout rule exactly as-is. Same system, new palette.

Target mapping:
| From (gold family) | To |
|---|---|
| `--nx-gold` `#facc15` / `rgb(250, 204, 21)` | cyan `#25e0ff` / `rgb(37, 224, 255)` |
| `--nx-gold-2` `#f59e0b` / `rgb(245, 158, 11)` | violet `#9a6bff` / `rgb(154, 107, 255)` |
| Any gradient midpoint tied to gold (e.g. `#fbbf24`) | drop the third stop — use a clean 2-stop `cyan → violet` gradient instead |
| Light gold tints used for icon/pill backgrounds (e.g. `#fde68a`) | corresponding light cyan or violet tint (pick whichever reads better per instance — cloud-hub file-type pills already use light blue/purple tints elsewhere in the file for consistency, follow that existing pattern) |

Leave as-is (already thematically consistent, not part of "gold branding," low priority to touch):
- `--nx-blue` `#38bdf8`, `--nx-purple` `#a78bfa` — already close to the new palette
- `--nx-green` `#22c55e`, `--nx-red` `#f87171` — generic status colors, not brand color

## Step 0 — full audit (required before any edit)
`premium.css` is 3477 lines and only partially reviewed so far. Before changing anything:
1. Search the entire file (and `style.css` / `design-system.css` too, in case gold leaks into those) for every gold-family literal:
   - `--nx-gold`, `--nx-gold-2` (variable definition + every `var(--nx-gold...)` usage)
   - `rgba(250, 204, 21` and `rgba(245, 158, 11` (hardcoded, any alpha)
   - `#facc15`, `#f59e0b`, `#fbbf24`, `#fde68a` and any other gold/amber hex you find (search broadly — `fbbf`, `fde6`, `f59e`, `facc` as partial matches to catch variants)
2. Report back a full list: file, line number, and the surrounding selector/rule for each hit, grouped by component (header, sidebar, buttons, toasts, confirm modal, cloud hub, anything else found in the unreviewed middle section of the file).
3. Do not proceed to Step 1 until this list is shared — the true scope (likely well beyond what's been seen so far) needs to be known before touching it.

## Step 1 — phased recolor (verify after each phase, don't do it all in one pass)
**Phase 1 — core chrome:** header (`.nexora-header`, `.nexora-logo`), sidebar (`.left-nav`, `.sidebar-brand`, `.nav-item.active`), buttons (`.btn-primary`, `#ask-nexora-btn`, focus-visible outlines).
→ Screenshot: header + sidebar + one primary button, before/after.

**Phase 2 — feedback systems:** toasts (`.nx-toast-*` — note this is an existing class prefix in `premium.css`, unrelated to the new `.nx-*` component classes, do not confuse the two), confirm modal (`.nx-confirm-*`).
→ Screenshot: one success toast, one warning toast, one confirm modal (default and danger variant).

**Phase 3 — data surfaces:** cloud hub table headers and file-type pills/icons (`.cloud-hub-*`), any dashboard cards or badges using gold found in Step 0's audit.
→ Screenshot: cloud hub table with a few different file-type pills visible.

**Phase 4 — sweep:** anything from the Step 0 audit list not covered by phases 1–3 (likely in the unreviewed middle section — forms, tables, other dashboard widgets). Fix, screenshot, confirm nothing was missed by re-searching for the original gold literals — the search should return zero hits when done.

## Step 2 — brand wordmark font
The `.nexora-logo::after` (header) and `.left-nav .sidebar-brand h2` (sidebar) elements render the "NEXORA" wordmark. Add `font-family: 'Orbitron', sans-serif;` to both — this requires the Orbitron Google Font link already added to `index.html` in the prior task (confirm it's still there). This is the one deliberate typographic accent; do not apply Orbitron anywhere else in the existing UI.

## Constraints (non-negotiable)
- No layout, spacing, sizing, or `!important` positioning rules change — color values only.
- No dummy/placeholder/sample data introduced anywhere.
- No change to backend logic, database schema, API contracts, or JS behavior — this is colors and one font-family addition only.
- Don't touch `--nx-blue` / `--nx-purple` / `--nx-green` / `--nx-red` unless Step 0's audit shows them being used as part of the gold branding system specifically (e.g. a gold-adjacent gradient) — if unsure, flag rather than guess.
- The earlier additive `.nx-theme` work (new-screen theme, `--nxt-*` tokens) is untouched by this task — this task only recolors the existing `--nx-gold` system, it does not remove or replace `nexora-theme.css`.

## Verification (required before marking any part "done")
- Step 0's audit list, in full, before any code changes.
- Before/after screenshot for each phase (1–4) as specified above.
- Final full-app screenshot pass: header, sidebar (active + inactive nav), primary/secondary/danger buttons, both toast types shown, confirm modal, cloud hub with visible file-type pills.
- A final re-search for the original gold literals (`250, 204, 21`, `245, 158, 11`, `#facc15`, `#f59e0b`) confirming zero remaining hits, or an explicit list of intentional exceptions with reasoning.
- No "done"/"verified" claim will be accepted without this proof attached.

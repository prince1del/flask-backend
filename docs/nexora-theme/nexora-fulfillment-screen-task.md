# NEXORA — Fulfillment screen (first .nx-theme build)

## Objective
Build/re-theme the Order Cycle pipeline view (FY → Distributor → Order Sheet → SO/CI/Filled-Order files) as the first screen using the new component system, wrapped in `.nx-theme`. This is additive — it does not touch the reskinned existing UI (header, sidebar, toasts, cloud hub, etc.) or any backend/reconciliation logic.

## Step 0 — discovery (required before building anything)
Don't assume the current state — confirm and report:
1. Does an Order Cycle / Fulfillment accordion UI already exist (FY → Distributor → Order Sheet → SO/CI/Filled-Order)? If yes: file/template path, route, and whether it's functional or a stub. If no: confirm it needs to be built from scratch and what backend endpoints already exist to drive it (list them).
2. What does the SO/CI-vs-Filled-Order mismatch data actually look like at the API/data layer right now — field names, where the mismatch percentage or flag comes from, and whether a "blocked" vs "flagged" distinction already exists in the backend logic (per the standing business rule: mismatches flag for reconciliation, they never block; only genuine duplicate document re-uploads block).
3. Confirm the real distributor/document quirks already validated (BND, DCA, KAG_AGRA parsing) — is there existing sample real data available in the founder's workspace to build/test this screen against, or does it need real data pulled from the live DB?

Report back before writing any markup — this determines whether Step 1 is "build new" or "re-skin existing."

## Step 1 — structure (visual-only re-skin)
Wrap the whole screen in `<div class="nx-theme"> ... </div>` (never on `<body>` — this is additive to one screen, not global).

Accordion levels, each as an expandable `.nx-card`:
- **FY** (top level)
  - **Distributor** (nested `.nx-card`, use `distributors.svg` as the node icon)
    - **Order Sheet** (nested `.nx-card`)
      - **SO / CI / Filled Order files** — leaf-level file rows, use `fulfillment.svg` for the group, `article-master.svg` only if referencing catalog-linked line items specifically

Use `.nx-nav-item`-style rows for each accordion header (icon + label + expand chevron), `.active`/expanded state gets the cyan glow treatment already defined in the CSS.

**Decision (locked):** Step 1 is visual-only — no discrepancy pills. Tree API does not return mismatch data; pills would invent meaning.

## Step 2 — mismatch / status representation
Deferred to `nexora-oc-discrepancy-pills-task.md`. Not part of the visual re-skin pass.

## Step 3 — icons
Use from `app/static/icons/nexora/`: `fulfillment.svg` (module/section), `distributors.svg` (distributor nodes), `article-master.svg` (only where line items tie to catalog entries), `analytics.svg` (if a reconciliation summary/rollup view is included). All at 17–20px, `currentColor`, no fill.

## Constraints (non-negotiable)
- No dummy/placeholder/mock order data, sample distributors, or fake mismatch figures — this screen must be built and verified against real data from the live DB, consistent with the founder-workspace rule that's applied throughout this project.
- No change to reconciliation logic, parsing logic, or the SO/CI/Filled-Order data pipeline — this is presentation-layer only, same as the reskin work.
- Don't touch anything outside the `.nx-theme`-wrapped container.
- If existing accordion functionality already works (per Step 0), preserve its behavior exactly — only the visual layer changes.

## Verification (required before marking any part "done")
- Step 0's findings, reported in full, before building.
- Screenshot of the accordion collapsed and expanded (at least down to the SO/CI/Filled-Order file level) on **real** Order Cycle folder data.
- Confirm which exact files were created/changed (paths).
- No "done"/"verified" claim without this proof attached.

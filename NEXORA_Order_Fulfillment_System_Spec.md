# NEXORA Order Fulfillment System — Phase 2 Implementation Spec
**Date:** 4 July 2026

## Core Principles

1. **Do not silently auto-link anything.**
   - System may suggest matches, but final confirmation must always be from user.

2. **Use multi-signal verification.**
   - For SO: match Buyer Code + Distributor Name.
   - For CI: match Contract Number + Distributor Name.
   - If both match → high-confidence confirmation.
   - If mismatch → warning + force manual selection.

3. **Preserve history.**
   - Do not delete old order sheets automatically when new version uploaded.
   - Multiple order sheets/categories/versions may stay active.

4. **Keep the system flexible.**
   - Do not hardcode only Bedsheet, Towel, TOB.
   - Future categories should be possible.

5. **Filled Order is not sale.**
   - Filled Order = distributor demand/order placed.
   - Must NOT count in achievement or revenue.
   - Achievement only after Commercial Invoice verified.

---

## Phase 2.1 — Order Sheet Master

### Database Table: `order_sheet_master`

Fields:
- `id` (Primary Key)
- `name` (user-given name, e.g., "AW26 Bedsheet", "AW26 TOB Revised 23-06-2026")
- `category` (Bedsheet, Towel, TOB, or future categories)
- `uploaded_at` (timestamp)
- `workspace_id` (multi-tenant scoping)
- `file_reference` (path or reference to uploaded file)
- `is_active` (boolean)

### UI Requirement

When uploading Order Sheet, ask user for:
- Order Sheet name
- Category
- Active/Inactive status if needed

### Parsing Requirement

Make header-row detection flexible. Do not assume row 2 only. Bedsheet, Towel, TOB, and future sheets may have different structures.

---

## Phase 2.2 — Distributor Buyer Code

### Database Change

Add new column in `master_distributors`:
- `buyer_code TEXT`

### Updates Required

- Migration
- `_initialize()` in CentralizedDB
- Distributor add/edit UI if applicable

---

## Phase 2.3 — Filled Order Distributor Assignment

### When uploading Filled Order:

1. Ask user to select distributor from `master_distributors`
2. Implement smart filename suggestion using `firm_nick_name`

### Example:
- File name: `BND.xlsx`
- Distributor nickname matches Bernina
- Show: "Is this order for Bernina?"
- Buttons: Yes (confirm) / No (choose manually)

**Do not auto-confirm silently.**

Save Filled Order category-wise and distributor-wise.

---

## Phase 2.4 — Sales Order PDF Linking

### When SO PDF uploaded:

1. Extract Buyer Code from PDF text
2. Extract Contract No / SO number
3. Match Buyer Code with `master_distributors.buyer_code`
4. Compare distributor/buyer name from PDF with matched distributor name

### If Buyer Code and Name match:
Show confirmation: "Is this SO for [Distributor Name]? Buyer Code and Name both matched."

### If mismatch:
Show warning: "Buyer Code suggests [X], but name suggests [Y]. Please confirm manually."

### Save:
- `distributor_id`
- `order_ref_no` / Contract No
- SO file reference
- parsed line items

**No silent linking allowed.**

---

## Phase 2.5 — Commercial Invoice Linking

### When CI PDF uploaded:

1. Extract Contract No
2. Match Contract No with saved SO/order_ref_no
3. Cross-check distributor/buyer name from CI
4. Show confirmation before linking

### If mismatch:
Show warning and require manual selection.

### After successful CI verification:
- Mark CI as verified
- Link CI to SO
- Trigger achievement generation

---

## Phase 2.6 — Cumulative Fulfillment Tracking

### Database Table: `order_fulfillment_items`

Fields:
- `id`
- `order_lifecycle_id` (FK)
- `product_code`
- `brand`
- `color`
- `ordered_qty`
- `fulfilled_qty` (cumulative)
- `remaining_qty` (computed)
- `workspace_id`

### Logic:
- Filled Order creates ordered quantity target
- Every matched SO updates fulfilled quantity
- Remaining quantity = ordered_qty - cumulative fulfilled_qty
- Dashboard shows distributor-wise fulfillment percentage

### Example:
```
Distributor X ordered 100 pcs.
SO1 fulfilled 30 pcs.
SO2 fulfilled 20 pcs.

Dashboard shows:
Fulfilled: 50 pcs
Pending: 50 pcs
Fulfillment: 50%
```

---

## Phase 2.7 — Material Code Decoder

### Create brand/color abbreviation mapping system

Example code: `BS03KSGRDSP7847PNK`

Decode:
- `BS03` = product prefix
- `KS` = King Bedsheet
- `GRDSP` = Grid Space
- `7847` = design number
- `PNK` = Pink

### Create mapping for:
- Brand abbreviations
- Color abbreviations
- Product/type abbreviations

Do not hardcode incomplete mappings. Keep editable/configurable.

---

## Phase 2.8 — Auto Achievement Generation

### After CI verified against SO:
Automatically create achievement record.

### Achievement data:
- Distributor = linked distributor
- Period = CI date
- Amount = verified CI total value
- Source = Commercial Invoice
- Reference = Contract No / Invoice No

Manual achievement entry remains available only for cases where CI data unavailable.

---

## Implementation Sequence

1. **Phase 2.1** — Order Sheet Master (Database + Parsing)
2. **Phase 2.2** — Distributor Buyer Code (Add column)
3. **Phase 2.3** — Filled Order Distributor Selection
4. **Phase 2.4** — Sales Order PDF Linking
5. **Phase 2.5** — Commercial Invoice Linking
6. **Phase 2.6** — Cumulative Fulfillment Tracking
7. **Phase 2.7** — Material Code Decoder
8. **Phase 2.8** — Auto Achievement Generation

**Build in small steps. Do not try to implement everything in one big change.**

---

## Pre-Implementation Checklist

Before coding each phase:

1. Inspect existing files and database tables
2. Reuse existing `order_lifecycle_tracking` where suitable
3. Keep `workspace_id` safety everywhere
4. Add migration safely
5. Test with existing real Bombay Dyeing sample files
6. Do not break the existing `/legacy` workflow

---

## Critical Instructions

- Do not touch, edit, rename, delete, move, or refactor unrelated files
- Only work on exact files required for each phase
- Inspect existing structure carefully before making changes
- Avoid disturbing any other working module, especially `/legacy` workflow

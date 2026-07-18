# NEXORA — Order Cycle: discrepancy pill wiring (Step 2)

## Prerequisite
Step 1 (visual-only re-skin of `#order-cycle-workspace`) is done and must be verified on real data (live machine with populated Order Cycle folder) before this step starts. If that verification hasn't happened yet, do it first.

## Objective
Surface reconciliation discrepancy flags on the Order Sheet node of the accordion, using a new, isolated, read-only endpoint — no changes to the existing `completeness/<distributor_id>` endpoint or its consumers, no changes to reconciliation logic itself.

## Join-key decision (locked after Step 0)
- **`order_sheet_name` alone is unsafe** — the same sheet name appears under many distributors by design.
- **Composite match key for accordion:** `(distributor_id, order_sheet_name, financial_year)` — all three.
- **FY is not a DB column on `order_lifecycle_tracking`.** Do **not** add it. Do **not** change the tracking write path or schema.
- **FY is derived at read time from file paths** (`sales_order_file_reference` / `commercial_invoice_file_reference` / other Order Cycle paths) containing `Order Cycle/{FY}/...`. This is presentation-layer only.
- Records where FY cannot be parsed go into an explicit **`unknown`** bucket — never merge those counts into a real FY's sheet totals.
- Prefer `order_sheet_id` in the query when present, but UI matching still uses the filesystem sheet **name** string plus distributor + FY.

## Step 1 — new endpoint (additive only)
Add a new, single-purpose, read-only endpoint — do not modify `completeness/<distributor_id>` or any existing route.

- Suggested route: `GET /api/v1/order-fulfillment/order-cycle/discrepancy-summary/<distributor_id>` (adjust to match existing naming conventions in `data.py` — your call, just keep it separate from `completeness`).
- Optional query param: `?fy=FY2026-27` so the accordion can request one FY at a time when a distributor node is expanded under that FY.
- Response shape, keyed by order sheet **within the requested FY** (and an `unknown` group if needed):
  ```json
  {
    "financial_year": "FY2026-27",
    "order_sheets": [
      {
        "order_sheet_name": "...",
        "financial_year": "FY2026-27",
        "flagged_item_count": 0,
        "pending_item_count": 0,
        "total_item_count": 0
      }
    ],
    "unknown_fy_order_sheets": []
  }
  ```
- Query: join `order_fulfillment_items` (`has_discrepancy`) with `order_lifecycle_tracking`, scope by `distributor_id`, derive FY from file paths, group by `(order_sheet_name, financial_year)`. Read-only — no writes, no changes to `_recheck_item_discrepancy()` or any reconciliation logic.
- This is a presentation-layer join. If it turns out the existing `completeness` query can be reused/called internally and just re-shaped for this response, that's fine — but the new route/response contract stays separate from `completeness`'s own contract.

### Pre-query check (required)
Before writing the query, run the **cross-FY collision test** on real data: same distributor + same `order_sheet_name` under two different FY folders. If such a case exists, prove the endpoint returns separate, correct counts for each FY. If no such case exists in the data, say so explicitly — do not skip the check.

## Step 2 — wire into the accordion
On Order Sheet node render (lazy — call once the distributor's sheets are visible under a known FY, not eagerly for the whole tree):
- Match pills with `(distributor_id, order_sheet_name, financial_year)` — all three.
- `flagged_item_count > 0` → `.nx-pill-warning` with the count (e.g. "2 flagged") — reconciliation flag only, never blocks anything.
- `flagged_item_count == 0` and `pending_item_count == 0` → `.nx-pill-success`, or no pill if that reads cleaner — your call.
- `pending_item_count > 0` (awaiting docs, not yet complete) → `.nx-pill-info` or `.nx-text-dim`, not a status color.
- `.nx-pill-danger` is never used on this screen — duplicate detection stays out of scope here, as already agreed.

Distributor-level and FY-level rollups are explicitly **out of scope for this pass** — sheet-level only. Don't add them speculatively.

## Constraints (non-negotiable)
- No change to `completeness/<distributor_id>`, `_recheck_item_discrepancy()`, or any reconciliation/parsing logic.
- No change to `order_lifecycle_tracking` schema or write path.
- No dummy data — verify against real `has_discrepancy` rows in the live DB, not fabricated counts.
- New endpoint is read-only — no write/update paths added.
- If FY derivation cannot cover a record, bucket as `unknown` — never silently fold into a real FY.

## Verification (required before marking any part "done")
- Cross-FY collision test result (found + separate counts proven, or explicitly "none in data").
- New endpoint's actual response on a real distributor with at least one known flagged item (counts matched against DB `has_discrepancy` rows directly).
- Screenshot: one order sheet with a warning pill (real flagged count), one with no flags, on live data.
- Confirm `completeness/<distributor_id>` behaves identically to before this change (unaffected).
- No "done"/"verified" claim without this proof attached.

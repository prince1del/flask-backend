# Order Bundle Matching — Global Rules (FO ↔ SO)

**Mirror of:** `Nexora20/docs/ORDER_BUNDLE_MATCHING_RULES.md`  
**Status:** Spec for implementation — **not coded yet**.

See the Nexora20 copy for the full document. Backend implements here when built:

- Stream classifier + PO family extractor on ingest  
- `order_bundles` / `order_bundle_streams` / `order_bundle_patterns` tables  
- APIs under `/api/v1/order-fulfillment/bundle/*`  
- Regression: BND AW26 Towel (`RFA 0381` regular + SPL)

**Hard rule:** Generic rules only — no `if supplier == BND` in production code.

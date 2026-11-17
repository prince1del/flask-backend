# NEXORA — 6 Core Generalization Features

**Canonical spec:** `C:\Users\princ\AndroidStudioProjects\Nexora20\docs\NEXORA_6_GENERALIZATION_FEATURES.md`

Edit the Nexora20 copy; this file is a mirror for backend developers working in `E:\centralized-db-system`.

**Build order:** Feature 2 → 3 → 1 → 4 → 5 → 6  
**Status:** Not started (saved 2026-08-29).

Key backend touchpoints today (do not break):

- `app/routes/data.py` — CI/SO upload, `parse_bombay_dyeing_so_ci_line_items`, `_upload_invoice_v2_impl`
- `filled_orders_routes.py` — FO upload
- Order match — `/api/v1/order-fulfillment/order-match/*`

New workspaces: `parser_mode = "ai_generalized"`. BD/HoP: `legacy_bd` until migration proven.

# Order Desk recycle store (delete → re-upload restores)

Implementation: `app/services/order_desk_archive.py` (single shared mechanism).

No Order Desk delete is permanently destructive any more. Every destructive path
snapshots what it removes into the `order_desk_archive` table first, and
uploading the same source data again restores the snapshot — so the FO ↔ SO ↔ CI
match, quantities and totals come back to the pre-delete state instead of only
what the uploaded file contains.

## Archived kinds

| kind | payload | entity_key | restored by |
|------|---------|-----------|-------------|
| `match_so` | that SO's `so_line_detail` rows + run metadata | SO number | SO Pack upload for that Filled Order |
| `match_run` | the run's match rows/totals (audit) | `run:<id>` | metadata only |
| `tracking` | `order_lifecycle_tracking` row + `order_fulfillment_items` + `achievements` + `distributor_payment_entries` + `processed_documents` | order ref no | SO PDF upload with the same order ref |
| `filled_order` | FO header + `filled_order_items` | `<distributor>\|<category>\|<season>` | FO workbook upload (re-points archives at the new FO id) |
| `match_run` (whole FO delete) | full run snapshot + SO numbers | same FO entity key | **FO re-upload** (re-links detached SO match) |
| `file` | recycled uploaded file reference | relative upload path | tracking restore (CI file) |

## Covered destructive paths

* `DELETE /api/v1/order-fulfillment/order-match/<run_id>` — per-SO and whole-run
  (`confirm_all=1`). The existing 409 `match_run_has_multiple_so` guard is
  unchanged.
* `POST /api/v1/order-fulfillment/order-match/delete-selected`
* `POST /api/v1/order-fulfillment/order-match/<run_id>/strip-so`
* SO revision `replace` / `split` inside
  `POST /api/v1/order-fulfillment/so-pack/match-filled-order` (audit snapshot)
* `DELETE /api/v1/order-fulfillment/tracking/<tracking_id>` and
  `POST /api/v1/order-fulfillment/tracking/delete-selected`
* `DELETE /api/v1/order-fulfillment/file` and the file cleanup that runs with a
  tracking delete — files are **moved** to
  `<upload root>/_nexora_recycle/<user_id>/…`, not unlinked.
* `DELETE /api/v1/filled-orders/<id>`, `POST /api/v1/filled-orders/delete-selected`,
  `DELETE /api/v1/filled-orders/<id>/items/<item_id>` and the
  `confirm_replace` branch of `POST /api/v1/filled-orders/upload`.

## Restore scope — nothing reappears behind the user's back

* `restore_scope='run'` — the destruction was wholesale (whole match run, bulk
  delete, tracking delete, FO delete). Any later upload for that FO / order ref
  may bring the rest of it back.
* `restore_scope='entity'` — the user singled the entity out (delete one SO,
  strip SO, replace with a revision). It only returns if that exact SO number /
  order ref is uploaded again.

Other guarantees:

* **Newer wins.** SO numbers present in the current upload are never restored
  from the archive; the file is the newer truth.
* **Idempotent.** Restored rows are stamped `restored_at` and skipped
  afterwards, and restore also skips anything already present, so uploading the
  same file twice cannot double a quantity or a value.
* **Per-user.** Every archive row carries the deleting user's `user_id` and
  every read filters on it — user A's archive can never be restored into user
  B's data, even for the same SO number, FO or order ref.
* An SO number already claimed by another run is never stolen back by a restore.

## Retention

`RETENTION_DAYS = 90`. `purge_expired()` drops archive rows older than that and
deletes the recycled files they own. `maybe_purge()` is called from the existing
`GET /api/v1/order-fulfillment/order-match/list` read path and is throttled to
at most one sweep per process every 6 hours — no cron job and no new UI.

## Tests

`tests/test_order_desk_recycle_restore.py`

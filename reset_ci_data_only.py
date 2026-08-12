import sqlite3

conn = sqlite3.connect('centralized_db.sqlite3')
c = conn.cursor()

TRACKING_ID = 1

print('--- BEFORE ---')
c.execute("""
    SELECT id, item_name, item_key, ci_qty, ci_value FROM order_fulfillment_items
    WHERE order_lifecycle_id = ?
""", (TRACKING_ID,))
for row in c.fetchall():
    print(' ', row)

print()
print('--- CLEANING ---')

# Remove the orphan row entirely (item_key is None — it only ever
# existed because of the page-break parsing bug, now fixed; it
# should not persist once the fix produces clean data).
c.execute("""
    DELETE FROM order_fulfillment_items
    WHERE order_lifecycle_id = ? AND item_key IS NULL AND so_qty IS NULL AND ordered_qty = 0
""", (TRACKING_ID,))
print('orphan rows deleted:', c.rowcount)

# Reset CI fields on the real Aster row back to None, so the
# upcoming re-upload starts fresh instead of accumulating on top of
# the old (page-break-corrupted) ci_qty/ci_value.
c.execute("""
    UPDATE order_fulfillment_items
    SET ci_qty = NULL, ci_value = NULL, has_discrepancy = 0, discrepancy_notes = NULL
    WHERE order_lifecycle_id = ?
""", (TRACKING_ID,))
print('rows reset:', c.rowcount)

conn.commit()

print()
print('--- AFTER ---')
c.execute("""
    SELECT id, item_name, item_key, ordered_qty, so_qty, ci_qty FROM order_fulfillment_items
    WHERE order_lifecycle_id = ?
""", (TRACKING_ID,))
for row in c.fetchall():
    print(' ', row)

conn.close()
print()
print('Done. Ordered/SO data untouched. Ready to re-upload the CI cleanly.')

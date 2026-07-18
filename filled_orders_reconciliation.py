"""
Bridge Article-Master-based filled_orders → order_fulfillment_items reconciliation.
"""

from __future__ import annotations

from typing import Any

import filled_orders_db as fodb
from order_item_keys import size_code_only_item_key


def _ordered_value_for_item(item: dict[str, Any]) -> float:
    qty = float(item.get("final_piece_qty") or 0)
    if qty <= 0:
        return 0.0
    rate = item.get("ptr")
    if rate is None:
        rate = item.get("mrp")
    if rate is None:
        rate = item.get("ex_mill_price")
    try:
        return qty * float(rate or 0)
    except (TypeError, ValueError):
        return 0.0


def _display_name_for_item(item: dict[str, Any]) -> str:
    parts = [item.get("brand"), item.get("size"), item.get("product_type")]
    label = " ".join(str(p).strip() for p in parts if p and str(p).strip())
    return label or (item.get("item_key") or "Filled order item")


def build_ordered_items_from_filled_order(conn, filled_order_id: int) -> list[dict[str, Any]]:
    """
    Aggregate filled_order_items by size-normalized item_key.
    Returns rows ready for upsert_order_lifecycle_item(source='ordered').
    """
    grouped: dict[str, dict[str, Any]] = {}
    for item in fodb.get_filled_order_items(conn, filled_order_id):
        raw_key = item.get("item_key")
        if not raw_key:
            continue
        norm_key = size_code_only_item_key(raw_key)
        qty = float(item.get("final_piece_qty") or 0)
        value = _ordered_value_for_item(item)
        if norm_key in grouped:
            grouped[norm_key]["qty"] += qty
            grouped[norm_key]["value"] += value
        else:
            grouped[norm_key] = {
                "item_name": _display_name_for_item(item),
                "item_key": norm_key,
                "qty": qty,
                "value": value,
            }
    return list(grouped.values())


def apply_filled_order_ordered_items(
    db,
    *,
    tracking_id: int,
    filled_order_id: int,
    workspace_id: str,
    conn,
) -> list[dict[str, Any]]:
    """Write ordered_qty/value from a saved filled order onto a tracking record."""
    ordered_rows = build_ordered_items_from_filled_order(conn, filled_order_id)
    item_results = []
    for row in ordered_rows:
        result = db.upsert_order_lifecycle_item(
            tracking_id=tracking_id,
            item_name=row["item_name"],
            source="ordered",
            qty=row["qty"],
            value=row["value"],
            workspace_id=workspace_id,
            item_key=row["item_key"],
        )
        item_results.append(result)
    return item_results


def flag_so_items_without_filled_order_match(
    db,
    *,
    tracking_id: int,
    so_line_items: list[dict[str, Any]],
    filled_order_id: int,
    workspace_id: str,
    conn,
) -> None:
    """Mark SO rows that have no corresponding filled-order line."""
    ordered_keys = {
        size_code_only_item_key(row["item_key"])
        for row in build_ordered_items_from_filled_order(conn, filled_order_id)
    }
    existing = db.list_order_lifecycle_items_for_tracking(tracking_id, workspace_id=workspace_id)
    for line in so_line_items:
        so_key = size_code_only_item_key(line.get("item_key"))
        if not so_key or so_key in ordered_keys:
            continue
        matched_row = next(
            (row for row in existing if size_code_only_item_key(row.get("item_key")) == so_key),
            None,
        )
        if matched_row and not (matched_row.get("ordered_qty") or 0):
            note = "No matching Filled Order item found"
            prior = matched_row.get("discrepancy_notes") or ""
            combined = f"{prior}; {note}" if prior and note not in prior else (prior or note)
            with __import__("sqlite3").connect(db.db_path) as note_conn:
                note_conn.execute(
                    "UPDATE order_fulfillment_items SET discrepancy_notes = ? WHERE id = ?",
                    (combined, matched_row["id"]),
                )
                note_conn.commit()

"""Bill numbers that never repeat, Cancel bill (owner notes #32 / #35, 21-11).

* A number belongs to one bill only: a live bill or a CANCELLED bill keeps it. Saving / editing / restoring a bill
  with a number another bill already has is refused ("same invoice to 2 customers is a crime").
* Delete (Recycle bin) frees the number — the next new bill can take it again ("bill 2 deleted → next is 2, not 3").
  Restoring that bill later is refused if its number was given to another bill meanwhile.
* Cancel keeps the number: the bill stops counting everywhere (stock, party balance, reports — same as delete) but
  stays in the bill list marked Cancelled, with the reason. That is the GST-safe way for a bill already handed out.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

# rows that still hold their number: not deleted, or deleted because they were cancelled
HOLDS_NUMBER = "((deleted_at IS NULL OR trim(deleted_at)='') OR cancelled_at IS NOT NULL)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_columns(conn: sqlite3.Connection) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(hop_party_transactions)")}
    for col in ("cancelled_at", "cancel_reason"):
        if col not in have:
            conn.execute(f"ALTER TABLE hop_party_transactions ADD COLUMN {col} TEXT")


def number_owner(conn: sqlite3.Connection, ws: str, txn_type: int, txn_number: str,
                 exclude_id: int | None = None) -> dict[str, Any] | None:
    """The bill (live or cancelled) that already has this number for this document type, else None."""
    ensure_columns(conn)
    num = str(txn_number or "").strip()
    if not num:
        return None
    sql = (f"SELECT id, txn_number, party_name, cancelled_at FROM hop_party_transactions WHERE workspace_id=? "
           f"AND txn_type=? AND lower(trim(txn_number))=lower(?) AND {HOLDS_NUMBER}")
    args: list[Any] = [ws, int(txn_type), num]
    if exclude_id:
        sql += " AND id!=?"
        args.append(int(exclude_id))
    r = conn.execute(sql + " LIMIT 1", args).fetchone()
    return dict(r) if r else None


def check_free(conn: sqlite3.Connection, ws: str, txn_type: int, txn_number: str, exclude_id: int | None = None) -> None:
    hit = number_owner(conn, ws, txn_type, txn_number, exclude_id)
    if hit:
        who = f" ({hit['party_name']})" if hit.get("party_name") else ""
        state = "a cancelled bill" if hit.get("cancelled_at") else "another bill"
        raise ValueError(f"Bill number {txn_number} is already used by {state}{who}. Use the next number.")


def cancel_document(conn: sqlite3.Connection, ws: str, txn_id: int, reason: str = "", *, actor: str = "") -> dict[str, Any]:
    """Cancel a bill: it stops counting (like delete) but keeps its number and stays in the list as Cancelled."""
    from app import hop_phase3_ext

    ensure_columns(conn)
    row = conn.execute("SELECT id, txn_number, deleted_at, cancelled_at FROM hop_party_transactions WHERE workspace_id=? AND id=?",
                       (ws, int(txn_id))).fetchone()
    if not row:
        raise LookupError("Bill not found")
    if row["cancelled_at"]:
        return {"party_txn_id": int(txn_id), "txn_number": row["txn_number"], "already_cancelled": True}
    if row["deleted_at"]:
        raise ValueError("This bill is in the Recycle bin — restore it first, then cancel it")
    hop_phase3_ext.soft_delete_document(conn, ws, int(txn_id), actor=actor or None)
    conn.execute("UPDATE hop_party_transactions SET cancelled_at=?, cancel_reason=? WHERE workspace_id=? AND id=?",
                 (_now(), str(reason or "").strip()[:200] or None, ws, int(txn_id)))
    conn.commit()
    return {"party_txn_id": int(txn_id), "txn_number": row["txn_number"], "cancelled": True}

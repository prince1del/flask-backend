"""Repair FO ↔ SO Order Match data after an accidental SO delete / re-upload.

Thin CLI wrapper — the logic lives in `app/services/fo_so_match_repair.py`, the
same module the app's silent auto-heal uses, so there is one implementation.

Symptoms this fixes (Bernina AW26 case):
  * one Filled Order ended up with several match runs (the re-upload created a
    second run instead of merging), so the UI reads a run that holds only the
    re-uploaded SO → spurious MISSING_ON_SO and a wrong / zero total;
  * fo_so_match_so_index still claims SO numbers whose run is gone, which makes
    a clean re-upload fail with 409 "Sales Order already uploaded";
  * run totals (so_qty / so_net_amount / counts) drifted from the stored lines.

SO lines that were physically deleted cannot be invented — after the repair the
user re-uploads those SO PDFs and they now merge in as Additional orders.

Dry run (default, safe):
    python scripts/repair_fo_so_match.py --distributor Bernina
Apply:
    python scripts/repair_fo_so_match.py --distributor Bernina --apply

Without --user-id the script (a local operator tool, not an HTTP caller) works
across every user in the database; pass --user-id to stay inside one user's rows.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import fo_so_match_db as matchdb  # noqa: E402
from app.services import fo_so_match_repair as repairsvc  # noqa: E402


def _db_path(explicit: str | None) -> str:
    if explicit:
        return explicit
    return os.getenv("DATABASE_PATH") or str(
        Path(__file__).resolve().parent.parent / "centralized_db.sqlite3"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db")
    parser.add_argument("--filled-order-id", type=int)
    parser.add_argument("--distributor")
    parser.add_argument("--user-id", type=int)
    parser.add_argument("--apply", action="store_true", help="write changes")
    args = parser.parse_args(argv)

    path = _db_path(args.db)
    conn = sqlite3.connect(path)
    try:
        matchdb.ensure_schema(conn)
        if args.user_id is not None:
            scope = repairsvc.RepairScope.for_user(args.user_id)
        else:
            # Operator scope: every user in this database.
            scope = repairsvc.RepairScope(user_id=0, global_scope=True)
        summary = repairsvc.repair(
            conn,
            scope=scope,
            filled_order_id=args.filled_order_id,
            distributor=args.distributor,
            apply=args.apply,
        )
        print(f"db={path}")
        print(
            f"stale SO index rows: {summary['orphan_index_rows']}"
            f"{'' if args.apply else ' (dry run)'}"
        )
        print(f"FO groups to repair: {summary['processed_orders']}")
        for report in summary["orders"]:
            print(json.dumps(report, indent=2, default=str))
    finally:
        conn.close()
    if not args.apply:
        print("\nDRY RUN — re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run sample reconciliation on the real files supplied by founder.

Usage from project root after copying module:
python scripts/run_reconciliation_sample.py \
  --master "Bedsheet SS-26 booking form.xlsx" \
  --filled "BND Order.xlsx" \
  --so "BND 102875606.pdf" \
  --ci "Commercial Invoice.PDF" \
  --out reconciliation_report.json
"""

from __future__ import annotations

import argparse
import json
from centralized_db_system.order_reconciliation import reconcile_order_chain


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", required=False)
    parser.add_argument("--filled", required=True)
    parser.add_argument("--so", required=False)
    parser.add_argument("--ci", required=False)
    parser.add_argument("--out", default="reconciliation_report.json")
    args = parser.parse_args()

    report = reconcile_order_chain(args.master, args.filled, args.so, args.ci)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, default=str, indent=2)
    print(f"Saved: {args.out}")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()

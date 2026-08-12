"""
Verify filled-order parsing against the 8 real distributor Excel files.
Run: python verify_filled_orders_real_files.py
"""

import sqlite3
from pathlib import Path

import pandas as pd

import article_master_db as amdb
import article_master_parser as amparser
import filled_orders_parser as foparser

DB_PATH = Path(__file__).resolve().parent / "centralized_db.sqlite3"
USER_ID = 2

FILES = [
    ("Bed", r"G:\My Drive\2026-2027\AW26 order sh\Bedsheet\Choice Corner.xlsx"),
    ("Bed", r"G:\My Drive\2026-2027\AW26 order sh\Bedsheet\DCA Order.xlsx"),
    ("Bed", r"G:\My Drive\2026-2027\AW26 order sh\Bedsheet\kag.xlsx"),
    ("Bed", r"G:\My Drive\2026-2027\AW26 order sh\Bedsheet\BND.xlsx"),
    ("Bath", r"G:\My Drive\2026-2027\AW26 order sh\Towel\KAG AGRA.xlsx"),
    ("Bath", r"G:\My Drive\2026-2027\AW26 order sh\Towel\ptj.xlsx"),
    ("Bath", r"G:\My Drive\2026-2027\AW26 order sh\Towel\savitri steel.xlsx"),
    ("Bath", r"G:\My Drive\2026-2027\AW26 order sh\Towel\BND.xlsx"),
]


def _load_sheet(path):
    with pd.ExcelFile(path) as xl:
        sheet = xl.sheet_names[0]
    raw_df = pd.read_excel(path, sheet_name=sheet, header=None)
    header_idx = amparser.detect_header_row(raw_df)
    header_row = raw_df.iloc[header_idx].tolist()
    col_mapping = amparser.map_columns_to_core(header_row)
    data_rows = raw_df.iloc[header_idx + 1:]
    valid_rows = [
        row for _, row in data_rows.iterrows()
        if amparser.is_data_row(row.tolist(), col_mapping)
    ]
    return header_row, col_mapping, valid_rows


def verify_bnd_sum_formula(header_row, col_mapping, valid_rows):
    """BND.xlsx: Additional Order Qty = Qty + Add for every comparable row."""
    labels = {i: str(header_row[i]).strip() for i in range(len(header_row))}
    idx = {lbl: i for i, lbl in labels.items() if lbl and lbl.lower() != "nan"}

    def col(name):
        for k, v in idx.items():
            if k.lower() == name.lower():
                return v
        return None

    qty_i = col("Qty")
    add_i = col("Add")
    total_i = col("Additional Order Qty")
    if qty_i is None or add_i is None or total_i is None:
        return None, "Qty/Add/Additional Order Qty columns not all present"

    comparable = matches = 0
    mismatches = []
    for _, row in enumerate(valid_rows):
        q = foparser._safe_float(row.iloc[qty_i]) if qty_i < len(row) else None
        a = foparser._safe_float(row.iloc[add_i]) if add_i < len(row) else None
        t = foparser._safe_float(row.iloc[total_i]) if total_i < len(row) else None
        if q is None or a is None or t is None:
            continue
        comparable += 1
        if abs((q + a) - t) <= 0.01:
            matches += 1
        else:
            mismatches.append((q, a, t))
    return {
        "comparable": comparable,
        "matches": matches,
        "mismatches": mismatches[:3],
    }, None


def main():
    conn = sqlite3.connect(DB_PATH)
    categories = amdb.get_all_categories(conn, USER_ID)
    key_fields_lookup = {c["category_name"]: c["key_fields"] for c in categories}

    print("=" * 72)
    print("FILLED ORDER — REAL FILE VERIFICATION (user_id=2 Article Master)")
    print("=" * 72)

    total_ordered = total_matched = 0
    results = []

    for expected_category, path in FILES:
        name = Path(path).name
        print(f"\n--- {name} (expected {expected_category}) ---")
        try:
            header_row, col_mapping, valid_rows = _load_sheet(path)
            print(f"  Data rows: {len(valid_rows)}")

            qty_det = foparser.detect_quantity_column(
                header_row, col_mapping, expected_category, valid_rows,
            )
            print(f"  Qty detection: {qty_det['status']}")
            if qty_det["status"] == "ok":
                print(f"    Column: {qty_det['column_label']}")
                if qty_det.get("auto_selected_reason"):
                    print(f"    Auto: {qty_det['auto_selected_reason']}")
                qty_idx = qty_det["column_index"]
            else:
                print(f"    Candidates: {[c['column_label'] for c in qty_det['candidates']]}")
                if qty_det.get("relationships"):
                    for r in qty_det["relationships"]:
                        print(f"    Relationship: {r['note']}")
                qty_idx = None

            if "BND" in name and expected_category == "Bed":
                bnd_result, err = verify_bnd_sum_formula(header_row, col_mapping, valid_rows)
                if err:
                    print(f"  BND formula: {err}")
                else:
                    print(
                        f"  BND formula Qty+Add=Additional: "
                        f"{bnd_result['matches']}/{bnd_result['comparable']} rows"
                    )
                    if bnd_result["mismatches"]:
                        print(f"    Sample mismatches: {bnd_result['mismatches']}")

            if qty_idx is None:
                results.append((name, 0, 0, "qty_column_needs_confirm"))
                continue

            parsed = foparser.build_filled_order_rows(
                valid_rows, header_row, col_mapping, qty_idx,
            )
            print(f"  Ordered lines (qty > 0): {len(parsed)}")

            key_fields = key_fields_lookup.get(expected_category, ["brand", "size"])
            matched = unmatched = flagged = 0
            units = {"bales": 0, "pieces": 0}
            unmatched_keys = []

            for row in parsed:
                m = foparser.match_and_normalize(
                    conn, amdb, USER_ID, row, key_fields, category=expected_category,
                )
                if m["matched"]:
                    matched += 1
                else:
                    unmatched += 1
                    unmatched_keys.append(m["item_key"])
                if not m["is_clean_bale_multiple"]:
                    flagged += 1
                units[m["detected_unit"]] = units.get(m["detected_unit"], 0) + 1

            rate = (matched / len(parsed) * 100) if parsed else 0
            print(f"  Matched: {matched}/{len(parsed)} ({rate:.1f}%)")
            print(f"  Unmatched: {unmatched} | Flagged (non-clean bale): {flagged}")
            print(f"  Units: bales={units.get('bales',0)} pieces={units.get('pieces',0)}")
            if unmatched_keys[:5]:
                print(f"  Sample unmatched keys: {unmatched_keys[:5]}")

            total_ordered += len(parsed)
            total_matched += matched
            results.append((name, len(parsed), matched, qty_det["status"]))

        except Exception as exc:
            print(f"  ERROR: {exc}")
            results.append((name, 0, 0, f"error: {exc}"))

    conn.close()

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    for name, ordered, matched, status in results:
        rate = f"{matched}/{ordered}" if ordered else "—"
        print(f"  {name:30} ordered={ordered:3} matched={rate:8} qty={status}")
    overall = (total_matched / total_ordered * 100) if total_ordered else 0
    print(f"\nOverall match rate: {total_matched}/{total_ordered} ({overall:.1f}%)")


if __name__ == "__main__":
    main()

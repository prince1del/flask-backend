"""Compare two Bed booking forms for price mismatches on matching items."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import article_master_db as amdb
import article_master_parser as amp

FILE_SS26 = Path(r"e:\test files\Bedsheet SS-26 booking form.xlsx")
FILE_AW26 = Path(r"g:\My Drive\2026-2027\AW26 order sh\Bedsheet\Order sheet AW26.xlsx")
KEY_FIELDS = ["brand", "TC", "size"]
LOOKUP = {"Bed": KEY_FIELDS}
PRICE_FIELDS = ["mrp", "ptr", "ex_mill_price", "bale_pack_size"]


def load_articles(path: Path):
    import pandas as pd

    with pd.ExcelFile(path) as xl:
        sheet = xl.sheet_names[0]
    articles, _, _, _, _ = amp.parse_article_sheet(
        str(path), sheet, LOOKUP, ["brand", "size"], forced_category="Bed"
    )
    return articles


def identity_match(a, b) -> bool:
    core_a = {"brand": a.get("brand"), "size": a.get("size")}
    core_b = {"brand": b.get("brand"), "size": b.get("size")}
    extra_a = a.get("extra_attributes") or {}
    extra_b = b.get("extra_attributes") or {}
    for field in KEY_FIELDS:
        av = amp.extract_key_field_value(field, core_a, extra_a)
        bv = amp.extract_key_field_value(field, core_b, extra_b)
        if field.lower() == "brand":
            if not amp.brands_match_fuzzy(av, bv):
                return False
        else:
            na = amp.normalize_key_part_value(field, av)
            nb = amp.normalize_key_part_value(field, bv)
            if na and nb and na != nb:
                return False
    return True


def find_match(target, pool):
    if target["item_key"] in {p["item_key"] for p in pool}:
        return next(p for p in pool if p["item_key"] == target["item_key"])
    for candidate in pool:
        if identity_match(target, candidate):
            return candidate
    return None


def tc_value(article):
    extra = article.get("extra_attributes") or {}
    return extra.get("TC") or extra.get("tc") or "—"


def main():
    for path in (FILE_SS26, FILE_AW26):
        print(f"{'OK' if path.exists() else 'MISSING'}: {path}")

    ss26 = load_articles(FILE_SS26)
    aw26 = load_articles(FILE_AW26)
    print(f"\nSS-26 booking form: {len(ss26)} rows")
    print(f"AW26 order sheet:   {len(aw26)} rows\n")

    mismatches = []
    matched_same = []
    only_aw26 = []

    for row_aw in aw26:
        row_ss = find_match(row_aw, ss26)
        if not row_ss:
            only_aw26.append(row_aw)
            continue
        diffs = []
        for field in PRICE_FIELDS:
            if not amdb._values_equal(field, row_ss.get(field), row_aw.get(field)):
                diffs.append((field, row_ss.get(field), row_aw.get(field)))
        if diffs:
            mismatches.append((row_ss, row_aw, diffs))
        else:
            matched_same.append(row_aw)

    only_ss26 = [row for row in ss26 if find_match(row, aw26) is None]

    print("=== SUMMARY ===")
    print(f"Matched, same prices:     {len(matched_same)}")
    print(f"Matched, price mismatch:  {len(mismatches)}")
    print(f"Only in SS-26:            {len(only_ss26)}")
    print(f"Only in AW26:             {len(only_aw26)}")
    print()

    print("=== PRICE MISMATCHES (SS-26 first upload vs AW26 second upload) ===")
    mismatches.sort(key=lambda t: (str(t[1].get("brand") or ""), str(t[1].get("size") or "")))
    for i, (row_ss, row_aw, diffs) in enumerate(mismatches, 1):
        print(
            f"{i}. {row_aw.get('brand')} | TC={tc_value(row_aw)} | Size={row_aw.get('size')}"
        )
        print(f"   SS-26 key: {row_ss.get('item_key')}")
        print(f"   AW26  key: {row_aw.get('item_key')}")
        for field, old, new in diffs:
            label = field.upper().replace("_", "-")
            print(f"   {label:12} SS-26={old}  ->  AW26={new}")
        print()

    if only_ss26:
        print("=== ONLY IN SS-26 (no match in AW26) ===")
        for row in sorted(only_ss26, key=lambda r: (str(r.get('brand') or ''), str(r.get('size') or ''))):
            print(f"- {row.get('brand')} | TC={tc_value(row)} | Size={row.get('size')} | MRP={row.get('mrp')}")

    if only_aw26:
        print("\n=== ONLY IN AW26 (no match in SS-26) ===")
        for row in sorted(only_aw26, key=lambda r: (str(r.get('brand') or ''), str(r.get('size') or ''))):
            print(f"- {row.get('brand')} | TC={tc_value(row)} | Size={row.get('size')} | MRP={row.get('mrp')}")


if __name__ == "__main__":
    main()

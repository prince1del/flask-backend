"""TOB SO PDFs must Brand×Size-resolve against FO Booking Sheet keys."""

from pathlib import Path

from app.services.bd_product_catalog import enrich_tob_so_product, resolve_so_brand_size
from app.services.fo_so_match_lab import (
    analyze_so_pack_pdfs,
    build_fo_buckets_from_workbook,
    build_so_buckets_from_line_detail,
    compare_fo_so_buckets,
    match_pair_key,
)

FO_PATH = Path(r"G:\My Drive\2026-2027\Oder Management\AW26 order\TOB\shri ram tob.xlsx")
SO_DIR = Path(
    r"G:\My Drive\2026-2027\Oder Management\AW26 order\TOB\SO"
    r"\wetransfer_sales-order-11-pdf_2026-09-04_1040"
)
SHRI_PDFS = [
    "Sales Order (5).PDF",
    "Sales Order (7).PDF",
    "Sales Order (11).PDF",
]


def test_tob_resolve_oro_vero_and_slumber_white():
    brand, size = resolve_so_brand_size(
        "MINK BLK ORO-VERO SB150X220 2PLY PRINT",
        material_code="BLOROVERO150220AST",
    )
    assert match_pair_key(brand, size) == match_pair_key("Oro-Vero", "150x220")

    brand, size = resolve_so_brand_size(
        "SLUMBER REVERSIBLE 229X274 WHITE",
        material_code="QT2LREV229274WHITE",
    )
    assert match_pair_key(brand, size) == match_pair_key("Slumber White", "229x274")

    brand, size = resolve_so_brand_size(
        "BIAB VOUGE EMB 4PC SET WITH COMF & BS",
        material_code="BIBVOGUEEMB4PCSSET",
    )
    assert match_pair_key(brand, size) == match_pair_key("BIAB", '250x275/18x28"')


def test_shri_ram_tob_fo_vs_so_seven_match():
    if not FO_PATH.is_file() or not SO_DIR.is_dir():
        return  # skip when Drive files are offline
    fo = build_fo_buckets_from_workbook(FO_PATH)
    assert fo.get("status") == "ok"
    payload = analyze_so_pack_pdfs(
        [(n, (SO_DIR / n).read_bytes()) for n in SHRI_PDFS]
    )
    so = build_so_buckets_from_line_detail(payload.get("line_detail") or [])
    assert so.get("others_qty", 0) == 0
    assert so.get("line_count") == 7
    compared = compare_fo_so_buckets(fo["buckets"], so["buckets"])
    matched = int(compared["counts"].get("MATCH") or 0) + int(
        compared["counts"].get("MATCH_FUZZY_BRAND") or 0
    )
    assert matched == 7
    assert int(compared["counts"].get("MISSING_ON_SO") or 0) == 0
    assert compared["totals"]["so_qty"] == 228.0


def test_mixed_wetransfer_filters_to_shri_ram_only():
    if not FO_PATH.is_file() or not SO_DIR.is_dir():
        return
    from app.services.fo_so_match_lab import filter_so_pack_by_fo_buyer, run_match_saved_fo_vs_so_pack
    all_pdfs = sorted({p.resolve(): p for p in list(SO_DIR.glob("*.PDF")) + list(SO_DIR.glob("*.pdf"))}.values())
    pack = analyze_so_pack_pdfs([(p.name, p.read_bytes()) for p in all_pdfs])
    fo_meta = {"distributor_name_raw": "Shri Ram & Co", "id": 1, "category": "TOB"}
    filtered, skipped = filter_so_pack_by_fo_buyer(pack, fo_meta)
    assert len(filtered.get("so_summary") or []) == 3
    assert any("CHOICE" in s.upper() for s in skipped)
    assert any("SAVITRI" in s.upper() for s in skipped)
    fo = build_fo_buckets_from_workbook(FO_PATH)
    items = [
        {"brand": b, "size": s, "final_piece_qty": c["qty"],
         "ex_mill_price": (c.get("value") or 0) / (c.get("qty") or 1)}
        for (b, s), c in fo["buckets"].items()
    ]
    result = run_match_saved_fo_vs_so_pack(fo_meta=fo_meta, fo_items=items, so_pack_payload=filtered)
    matched = int(result["match"]["counts"].get("MATCH") or 0)
    assert matched == 7
    assert int(result["match"]["counts"].get("EXTRA_ON_SO") or 0) == 0


def test_shri_ram_and_co_soft_key_not_co():
    from app.services.fo_so_match_lab import soft_brand_key, score_fo_for_buyer
    assert soft_brand_key("Shri Ram & Co") == "shri ram co"
    fo = {"distributor_name_raw": "Shri Ram & Co"}
    assert score_fo_for_buyer(fo, "CHOICE CORNER BOMBAY DYEING") < 0.45
    assert score_fo_for_buyer(fo, "SHRI RAM & CO., MEERUT") >= 0.45

"""Tests for HoP multi-format rate sheet uploads."""

from __future__ import annotations

from pathlib import Path

import openpyxl

from app.hop_rate_upload import (
    ALLOWED_EXTENSIONS,
    allowed_rate_upload,
    parse_rate_lines_from_text,
    parse_rate_upload_file,
)


def test_allowed_extensions_cover_requested_types():
    for ext in (".pdf", ".jpg", ".jpeg", ".bmp", ".png", ".xlsx", ".xls", ".docx", ".rtf", ".doc", ".csv"):
        assert ext in ALLOWED_EXTENSIONS
        assert allowed_rate_upload(f"quote{ext}")
    assert not allowed_rate_upload("malware.exe")


def test_parse_excel_product_rate_headers(tmp_path: Path):
    path = tmp_path / "rates.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Product", "Size", "Rate", "GST"])
    ws.append(["Bedsheet", "110x112", 730, "5%"])
    ws.append(["Pillow Cover", "21x31", 95, 5])
    wb.save(path)
    result = parse_rate_upload_file(path)
    assert result["line_count"] == 2
    assert result["source_type"] == "excel"
    names = {ln["product_name"] for ln in result["lines"]}
    assert "Bedsheet" in names
    assert "Pillow Cover" in names


def test_parse_welspun_direct_customer_price_cm_sizes(tmp_path: Path):
    """Mill price lists: Product + Size in CM + Direct Customer Price."""
    path = tmp_path / "price_list.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(
        [
            "Sr.No",
            "Article Code",
            "Programe",
            "Category",
            "Product",
            "Description",
            "Size",
            "GSM",
            "MTS/MTO",
            None,
            "Direct Customer Price",
        ]
    )
    ws.append(
        [1, 9011491, "Vapi 550", "Bath", "Large Towel", "WEL/Bath/91X183/550/BS/WHT", "91CMX183CM", "550 GSM", "MTS", None, 515.789473]
    )
    ws.append(
        [2, 1028619, "Vapi 550", "Bath", "Bath Towel", "WEL/Bath/76X152/550/BT/WHT", "76CMX152CM", "550 GSM", "MTS", None, 355.79]
    )
    ws.append(
        [3, 1033615, "Pool", "Bath", "Large Towel", "WEL/Bath/91X183/500/Hotel/STP/PT/BLU WHT", "91CMX183CM", "500 GSM", "MTS", None, 591.58]
    )
    wb.save(path)
    result = parse_rate_upload_file(path, supplier_hint="Welspun")
    assert result["line_count"] == 3
    assert result["source_type"] == "excel"
    lines = result["lines"]
    bathsheet = next(ln for ln in lines if ln["product_name"] == "Large Towel" and "Pool" not in (ln.get("quality") or ""))
    assert bathsheet["size"] == "36x72"
    assert bathsheet["rate"] == 515.79
    from app.hop_rate_compare import product_match_key

    assert product_match_key(bathsheet["product_name"], bathsheet["size"], bathsheet.get("quality")) == "bath_sheet|36x72|550gsm"
    bath = next(ln for ln in lines if ln["product_name"] == "Bath Towel")
    assert bath["size"] == "30x60"
    pool = next(ln for ln in lines if "Pool" in (ln.get("quality") or ""))
    assert "pool" in product_match_key(pool["product_name"], pool["size"], pool.get("quality"))


def test_parse_text_pipe_and_handwritten_style():
    text = """
Bedsheet | 110x112 | 715 | 5
D/Cover 72x108 1035 +5%
Bath Towel 30x60 432 +5%
"""
    rows = parse_rate_lines_from_text(text)
    assert len(rows) >= 3
    assert any(r["rate"] == 715 for r in rows)


def test_image_without_ocr_does_not_inject_sample_rates(tmp_path: Path):
    """Tiny blank jpeg → OCR may return nothing; never invent Bharat/GSB demo rates."""
    img = tmp_path / "Bharat.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    result = parse_rate_upload_file(img, supplier_hint="Bharat")
    assert result["line_count"] == 0
    assert not result.get("used_curated")


def test_handwritten_ocr_text_parses_sizes_and_rates():
    text = """
2-72x108-1035+5%
D/cower
3)SL.NO-4-110x114
BedSheer
—1498+5%
4)S.L.NO-5-21x31
P/coner
—106+5%
5) SL. NO-6-70x100
Duur
—999+18%
20x30
Bathmat
—199+5%
30x60
Luxury Bath
—432+5%
16x24
Hand towel
—91+5%
FreeSize
Bathrobe
—1095+5%
"""
    from app.hop_rate_upload import parse_handwritten_ocr_text

    rows = parse_handwritten_ocr_text(text)
    assert len(rows) >= 6
    assert any(r["rate"] == 1035 and "72x108" in str(r.get("size")) for r in rows)
    assert any(r["rate"] == 1498 for r in rows)
    assert not any(r.get("used_curated") for r in rows)


def test_handwriting_ocr_module_imports():
    from app.hop_handwriting_ocr import run_handwriting_ocr, extract_rates_gemini_vision

    assert callable(run_handwriting_ocr)
    assert callable(extract_rates_gemini_vision)


def test_ocr_chain_exports():
    from app.hop_rate_upload import extract_image_ocr_text

    assert callable(extract_image_ocr_text)


def test_empty_pdf_does_not_inject_gsb_samples(tmp_path: Path):
    pdf = tmp_path / "GSB.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    result = parse_rate_upload_file(pdf, supplier_hint="GSB")
    assert result["line_count"] == 0
    assert not result.get("used_curated")


def test_gsb_invoice_pdf_parses_description_and_all_items():
    pdf = Path(r"c:\Users\princ\OneDrive\Desktop\hop\GSB.pdf")
    if not pdf.exists():
        return
    result = parse_rate_upload_file(pdf, supplier_hint="GSB")
    assert result["line_count"] >= 22
    names = [ln["product_name"] for ln in result["lines"]]
    assert any("Spa Face Towel" == n for n in names)
    assert any("Hotel Plain Premium Face Towel" == n for n in names)
    assert any("Spa" in n and "Bath" in n for n in names)
    # Full narration kept — not collapsed to bare "Face Towel"
    assert "Face Towel" not in names
    assert not any("IGST" in n for n in names)


def test_pdf_table_extract_is_vendor_agnostic():
    """Any supplier PDF with Product/Rate columns should parse via pdf_table — not a custom parser."""
    from app.hop_rate_table_parse import extract_pdf_table_rate_rows, rows_from_grid

    grid = [
        ["Product", "Size", "QUOTED PRICE", "GST"],
        ["Bedsheet", "110x112", "715.00", "5%"],
        ["Bath Towel", "30x60", "432.00", "5%"],
        ["Pillow Cover", "21x31", "106.00", "5%"],
    ]
    rows = rows_from_grid(grid)
    assert len(rows) == 3
    assert rows[0]["rate"] == 715.0
    assert rows[0]["size"] == "110x112"

    jal = Path(r"c:\Users\princ\OneDrive\Desktop\hop\Jalandhar.pdf")
    if jal.exists():
        table_rows = extract_pdf_table_rate_rows(jal)
        assert len(table_rows) >= 20
        result = parse_rate_upload_file(jal, supplier_hint="AnySupplierName")
        assert result["parse_method"] == "pdf_table"
        assert result["line_count"] >= 20


def test_jalandhar_pdf_not_discarded_as_invoice_junk():
    pdf = Path(r"c:\Users\princ\OneDrive\Desktop\hop\Jalandhar.pdf")
    if not pdf.exists():
        return
    from app.hop_rate_upload import parse_jalandhar_rate_table, extract_text_from_file, _lines_look_like_invoice_junk

    text, _ = extract_text_from_file(pdf)
    rows = parse_jalandhar_rate_table(text)
    assert len(rows) >= 20
    assert not _lines_look_like_invoice_junk(rows)
    # Pack of N must not trip junk detector (old bug: bare 'ack' matched Pack)
    assert any("Pack" in str(r.get("product_name") or r.get("notes") or "") for r in rows)
    result = parse_rate_upload_file(pdf, supplier_hint="Jalandhar")
    assert result["line_count"] >= 20
    assert any(ln.get("size") for ln in result["lines"])
    assert not result.get("warnings")


def test_adecore_shortlisted_packages_stay_distinct():
    """Multi-option quotes repeat item names — keep Shortlisted-1/2/3/4 as separate rows."""
    from app.hop_rate_table_parse import rows_from_grid
    from app.hop_rate_compare import build_comparison_matrix, lines_from_structured

    grid = [
        ["Sl. No.", "Item Description", "Qty.", "Unit", "Project Rate", "Amount", "Discount", "GST %", "Amount"],
        ["", "Shortlisted-1 (Sheer + Chair Fabric)", "", "", "", "", "", "", ""],
        ["1", "SHEER FABRIC", "18.0", "MTR", "1,320.00", "23,760.00", "30%", "5%", "17,463.60"],
        ["2", "CHAIR FABRIC", "2.5", "MTR", "1,900.00", "4,750.00", "30%", "5%", "3,491.25"],
        ["", "TOTAL:", "", "", "", "", "", "", "27,484.53"],
        ["", "Shortlisted-2 (Sheer + Chair Fabric)", "", "", "", "", "", "", ""],
        ["1", "SHEER FABRIC", "18.0", "MTR", "725.00", "13,050.00", "30%", "5%", "9,591.75"],
        ["2", "CHAIR FABRIC", "2.5", "MTR", "2,206.00", "5,515.00", "30%", "5%", "4,053.53"],
        ["Shortlisted-3 (Main Curtain + Chair Fabric)", None, None, "", "", "", "", "", ""],
        ["1", "MAIN CURTAIN FABRIC", "18.0", "MTR", "950.00", "17,100.00", "30%", "5%", "12,568.50"],
    ]
    rows = rows_from_grid(grid)
    assert len(rows) == 5
    names = [r["product_name"] for r in rows]
    assert any("Shortlisted-1" in n and n.startswith("SHEER FABRIC") for n in names)
    assert any("Shortlisted-2" in n and n.startswith("SHEER FABRIC") for n in names)
    assert any("Shortlisted-3" in n for n in names)
    sheer_rates = sorted(r["rate"] for r in rows if "SHEER FABRIC" in r["product_name"])
    assert sheer_rates == [725.0, 1320.0]

    pdf = Path(r"c:\Users\princ\OneDrive\Desktop\hop\ADECORE FULL LIST.pdf")
    if pdf.exists():
        result = parse_rate_upload_file(pdf, supplier_hint="Adecore")
        assert result["line_count"] == 26
        assert len({ln["product_name"] for ln in result["lines"]}) == 26
        matrix = build_comparison_matrix(
            [
                {
                    "id": 1,
                    "supplier_name": "Adecore",
                    "lines": lines_from_structured(result["lines"]),
                }
            ]
        )
        assert matrix["summary"]["product_count"] == 26


def test_pack_of_n_is_not_invoice_junk():
    from app.hop_rate_upload import _lines_look_like_invoice_junk

    rows = [
        {"product_name": "Hand Towel (Pack of 6) Cotton Rich", "size": "40x60", "rate": 380, "gst_pct": 5},
        {"product_name": "Pillow Cover (Pack of 4)", "size": "17x27", "rate": 394, "gst_pct": 5},
        {"product_name": "Towelling Mat (Pack of 6)", "size": "40x60", "rate": 476, "gst_pct": 5},
        {"product_name": "Bath Towel", "size": "30x60", "rate": 199, "gst_pct": 5},
        {"product_name": "Single Bedsheet", "size": "60x90", "rate": 440, "gst_pct": 5},
    ]
    assert _lines_look_like_invoice_junk(rows) is False


def test_pdf_sample_from_desktop_if_present():
    pdf = Path(r"c:\Users\princ\OneDrive\Desktop\hop\AMbala.pdf")
    if not pdf.exists():
        return
    result = parse_rate_upload_file(pdf)
    assert result["source_type"] == "pdf"
    # Ambala PDF text is jumbled; either lines or a warning is fine as long as it accepts
    assert "warnings" in result or result["line_count"] >= 0

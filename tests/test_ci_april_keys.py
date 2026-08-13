"""Global CI key/brand rules — April PDFs are samples, not special cases."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routes.data import (
    _ci_line_pack_signature,
    _ci_lines_contradict_pdf_text,
    _enrich_ci_header_from_text,
    _refresh_saved_ci_lines,
    extract_order_sheet_item_key,
    parse_bombay_dyeing_so_ci_line_items,
)
from centralized_db_system.db import CentralizedDB

APRIL = Path(r"G:\My Drive\2026-2027\CI\April 2026\CI")


def test_glued_dbset_and_flora_sb_keys():
    assert extract_order_sheet_item_key(
        "BLUMEN 1+2 DBSET 224X254 7979BGE 104TC"
    ) == "BLUMEN|104|DB"
    assert extract_order_sheet_item_key(
        "BLUMEN 1+1 SBSET 140X224 7979BGE 104TC"
    ) == "BLUMEN|104|SB"
    assert extract_order_sheet_item_key(
        "EPIGRAM 1+1 SBSET 150X224 7684 PUR 120TC"
    ) == "EPIGRAM|120|SB"
    assert extract_order_sheet_item_key(
        "CARDINAL 1+2 DBSET 224X254 7747CRL 120TC"
    ) == "CARDINAL|120|DB"
    assert extract_order_sheet_item_key(
        "FLORA SB 2+2 150 X 224 BLD 120TC"
    ) == "FLORA|120|SB"
    assert extract_order_sheet_item_key(
        "FLORENTINE 1+2DB 228X254 7967BLU 144TC"
    ) == "FLORENTINE|144|DB"
    assert extract_order_sheet_item_key(
        "ALLURE DUVET CVR 245X270 7690BRW144T C"
    ) == "ALLURE|144|DUVET"
    assert extract_order_sheet_item_key(
        "AXIA COMFERTOR 220X240 7672 PST 104TC"
    ) == "AXIA|104|COMF"
    assert extract_order_sheet_item_key(
        "SAPPHIRE TROUSSEAU 07PCS 7720MRN 210TC"
    ) == "SAPPHIRE|210|TRS"
    assert extract_order_sheet_item_key(
        "COTTON COMFORT DB 1+2 224x254 BLD 180TC"
    ) == "COTTON COMFORT|180|DB"
    assert extract_order_sheet_item_key(
        "COTTON COMFORT KS 1+2 274x274 BLD 180TC"
    ) == "COTTON COMFORT|180|KS"
    assert extract_order_sheet_item_key(
        "COTTON COMFORT SB 2+2 150x274 BLD 180TC"
    ) == "COTTON COMFORT|180|SB"
    # Must not become ASTER
    assert not str(extract_order_sheet_item_key("FLORA SB 2+2 150 X 224 BLD 120TC")).startswith("ASTER")


def test_refresh_saved_lines_is_global_not_invoice_specific():
    """Any saved DBSET/Flora row is re-keyed; Aster AM match is dropped."""
    lines = [
        {
            "item_name": "BLUMEN 1+2 DBSET 224X254 7979BGE 104TC",
            "item_key": None,
            "article_match": {
                "status": "matched",
                "article": {"brand": "Aster", "category": "Bed"},
            },
            "article_id": 99,
        },
        {
            "item_name": "FLORA SB 2+2 150 X 224 BLD 120TC",
            "item_key": "FLORA SB|120|SB",
            "article_match": {
                "status": "matched",
                "article": {"brand": "Aster", "category": "Bed"},
            },
        },
        {
            "item_name": "ASTER 1+2 DB SET 224X244 7985BLU 100TC",
            "item_key": "ASTER|100|DB",
            "article_match": {
                "status": "matched",
                "article": {"brand": "Aster", "category": "Bed"},
            },
        },
    ]
    out, changed = _refresh_saved_ci_lines(lines)
    assert changed
    assert out[0]["item_key"] == "BLUMEN|104|DB"
    assert "article_match" not in out[0]
    assert out[1]["item_key"] == "FLORA|120|SB"
    assert "article_match" not in out[1]
    assert out[2]["item_key"] == "ASTER|100|DB"
    assert out[2]["article_match"]["article"]["brand"] == "Aster"


def test_blumen_single_lines_contradict_double_invoice():
    assert _ci_line_pack_signature("BLUMEN 1+2 DBSET 224X254 7979BGE 104TC") == "1+2DB"
    assert _ci_line_pack_signature("BLUMEN 1+1 SBSET 140X224 7979BGE 104TC") == "1+1SB"
    pdf = "Invoice 1400009337 BLUMEN 1+2 DBSET 224X254 7979BGE 104TC"
    assert _ci_lines_contradict_pdf_text(
        [{"item_name": "BLUMEN 1+1 SBSET 140X224 7979BGE 104TC"}],
        pdf,
    )
    assert not _ci_lines_contradict_pdf_text(
        [{"item_name": "BLUMEN 1+2 DBSET 224X254 7979BGE 104TC"}],
        pdf,
    )


def test_flora_lines_contradict_cotton_comfort_invoice_text():
    pdf_text = "Invoice No.: 1400009372 COTTON COMFORT DB 1+2 224x254 BLD 180TC"
    flora = [{"item_name": "FLORA DB 1+2 224X254 BLD 180TC"}]
    cotton = [{"item_name": "COTTON COMFORT DB 1+2 224x254 BLD 180TC"}]
    assert _ci_lines_contradict_pdf_text(flora, pdf_text)
    assert not _ci_lines_contradict_pdf_text(cotton, pdf_text)


def test_total_pieces_overwrites_truncated_saved_header():
    header = _enrich_ci_header_from_text(
        "Total Pieces : 1,188\nInvoice Total : 723492",
        {"total_pieces": 1},
    )
    assert header["total_pieces"] == 1188


def test_total_pieces_keeps_thousands_comma():
    header = _enrich_ci_header_from_text("Total Pieces : 1,188\nInvoice Total : 7,23,492.00")
    assert header["total_pieces"] == 1188


def test_comfertor_and_trousseau_folders():
    db = CentralizedDB(":memory:")
    assert db._ci_line_category_label(
        {"item_name": "AXIA COMFERTOR 220X240 7672 PST 104TC"}
    ) == "TOB"
    assert db._ci_line_category_label(
        {"item_name": "SAPPHIRE TROUSSEAU 07PCS 7720MRN 210TC"}
    ) == "Bed"


def test_april_blumen_epigram_flora_keys_from_pdf():
    samples = {
        APRIL / "RDS CI -No. 1400009337.PDF": ("BLUMEN", 18),
        APRIL / "RDS CI -No. 1400009161 Dated  02042026.PDF": ("EPIGRAM", 9),
        APRIL / "RDS CI -No. 1400009377.PDF": ("FLORA", 1),
        APRIL / "RDS CI -No. 1400009372.PDF": ("COTTON COMFORT", 3),
        APRIL / "RDS CI -No. 1400009227.PDF": ("CARDINAL", 8),
    }
    for path, (brand, min_lines) in samples.items():
        if not path.exists():
            continue
        items = parse_bombay_dyeing_so_ci_line_items(path, "CI")
        assert len(items) >= min_lines, path.name
        keyed = [it for it in items if it.get("item_key")]
        assert keyed, f"{path.name} still has no item_key"
        assert all(
            str(it["item_key"]).startswith(brand) for it in keyed
        ), f"{path.name} keys={[it.get('item_key') for it in items[:3]]}"
        assert all(
            not str(it.get("item_key") or "").startswith("ASTER")
            for it in items
        )
        assert all(
            brand in str(it.get("item_name") or "").upper() for it in items
        )

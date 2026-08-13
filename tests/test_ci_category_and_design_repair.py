import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routes.data import parse_bombay_dyeing_so_ci_line_items, _ci_design_colour_tokens
from centralized_db_system.db import CentralizedDB


def test_ci_categories_for_april_samples(tmp_path):
    db = CentralizedDB(str(tmp_path / "ci_cat.sqlite3"))
    samples = {
        r"G:\My Drive\2026-2027\CI\April 2026\CI\RDS CI -No. 1400009364.PDF": "Bed",
        r"G:\My Drive\2026-2027\CI\April 2026\CI\RDS CI -No. 1400009337.PDF": "Bed",
        r"G:\My Drive\2026-2027\CI\April 2026\CI\New folder\RDS CI -No. 1400009227.PDF": "Bed",
        r"G:\My Drive\2026-2027\CI\April 2026\CI\New folder\RDS CI -No. 1400009162.PDF": "Bed",
        r"G:\My Drive\2026-2027\CI\April 2026\CI\New folder\RDS CI -No. 1400009254.PDF": "Bath",
        r"G:\My Drive\2026-2027\CI\April 2026\CI\New folder\RDS CI -No. 1400009223.PDF": "Bath",
        r"G:\My Drive\2026-2027\CI\April 2026\CI\New folder\RDS CI -No. 1400009213.PDF": "Bath",
        r"G:\My Drive\2026-2027\CI\April 2026\CI\New folder\RDS CI -No. 1400009260.PDF": "TOB",
        r"G:\My Drive\2026-2027\CI\April 2026\CI\New folder\RDS CI -No. 1400009159 Dated  02042026.PDF": "TOB",
    }
    for path, expected in samples.items():
        if not Path(path).exists():
            continue
        items = parse_bombay_dyeing_so_ci_line_items(path, "CI")
        assert items, path
        cats = {db._ci_line_category_label(it) for it in items}
        assert cats == {expected}, f"{path} -> {cats}, want {expected}"


def test_truncated_design_colour_recovered_from_pdf_text():
    aster = r"G:\My Drive\2026-2027\CI\April 2026\CI\RDS CI -No. 1400009328.PDF"
    blumen = r"G:\My Drive\2026-2027\CI\April 2026\CI\RDS CI -No. 1400009337.PDF"
    if not Path(aster).exists():
        return
    aster_items = parse_bombay_dyeing_so_ci_line_items(aster, "CI")
    assert all(_ci_design_colour_tokens(it["item_name"]) for it in aster_items)
    colours = set()
    for it in aster_items:
        for tok in _ci_design_colour_tokens(it["item_name"]):
            if tok.startswith("7990"):
                colours.add(tok)
    assert colours == {"7990BGE", "7990LLC", "7990PCH"}

    if not Path(blumen).exists():
        return
    blumen_items = parse_bombay_dyeing_so_ci_line_items(blumen, "CI")
    colours = set()
    for it in blumen_items:
        for tok in _ci_design_colour_tokens(it["item_name"]):
            if tok.startswith("7984"):
                colours.add(tok)
    assert colours == {"7984BLU", "7984BRW", "7984PUR"}


def test_ci_page_break_stitches_any_serial_on_april_invoices():
    """Any SN can split; leftover next-page cells must join that line, not be dropped."""
    from pathlib import Path as P

    root = P(r"G:\My Drive\2026-2027\CI\April 2026\CI")
    if not root.exists():
        return
    truncated = []
    for path in sorted(set(root.rglob("*.PDF")) | set(root.rglob("*.pdf"))):
        items = parse_bombay_dyeing_so_ci_line_items(str(path), "CI")
        for idx, item in enumerate(items, 1):
            name = item.get("item_name") or ""
            upper = name.upper()
            # Page-splits look like "ASTER 1+2 DB SET 224X244" / "ALLURE DUVET CVR 245X270"
            # — size is the last token, design/colour is on the next page.
            ends_on_size = bool(re.search(r"\d{2,4}\s*[Xx×]\s*\d{2,4}\s*$", upper))
            looks_split_family = any(
                tok in upper
                for tok in ("1+2", "1+1", "DBSET", "SBSET", "DB SET", "DUVET")
            )
            if (
                ends_on_size
                and looks_split_family
                and not _ci_design_colour_tokens(name)
            ):
                truncated.append(f"{path.name} SN{idx} {name}")
    assert not truncated, "page-split lines still missing design/colour:\n" + "\n".join(truncated)


def test_ci_line_16_page_break_stitches_next_page_remainder():
    """SN 16 description splits: page 2 has 224X244, page 3 has 7990BGE 100TC."""
    path = r"G:\My Drive\2026-2027\CI\April 2026\CI\RDS CI -No. 1400009328.PDF"
    if not Path(path).exists():
        return
    items = parse_bombay_dyeing_so_ci_line_items(path, "CI")
    assert len(items) == 18
    assert items[15]["item_name"] == "ASTER 1+2 DB SET 224X244 7990BGE 100TC"
    assert _ci_design_colour_tokens(items[15]["item_name"]) == ["7990BGE"]

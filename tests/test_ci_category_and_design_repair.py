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

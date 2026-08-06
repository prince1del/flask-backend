"""TOB + Pillow Article Master teaching locks."""

from pathlib import Path

import article_master_parser as amparser

TOB_SRC = Path(
    r"G:\My Drive\2026-2027\Oder Management\AW26 order\TOB"
    r"\AW-26 TOB Revised Booking Sheet_23.06.2026.xlsx"
)
PILLOW_SRC = Path(
    r"G:\My Drive\2026-2027\Oder Management\AW26 order\Pillow"
    r"\Pillow  Booking Sheet_05.05.2026.xlsx"
)


def test_tob_blend_composition():
    assert amparser.build_tob_blend("100% Polyster", "SNL-PLY", 950) == (
        "100% Polyester Single Ply, 950"
    )
    assert amparser.build_tob_blend("100% Polyster", "1-Ply", "1kg") == (
        "100% Polyester 1 Ply, 1kg"
    )
    assert amparser.build_tob_blend(
        "Knitted With Alovera Finish", None, "750grm",
    ) == "Knitted With Aloe Vera Finish, 750grm"


def test_tob_product_and_category():
    assert amparser.normalize_product_spelling("Bed in a Bga") == "Bed in a Bag"
    assert amparser.detect_category_for_text("Bed in a Bga") == "TOB"
    assert amparser.detect_category_for_text("Bed in a Bag") == "TOB"
    assert amparser.detect_category_for_text("BIAB micro") == "TOB"
    assert amparser.detect_category_for_text("All Season Blanket") == "TOB"
    assert amparser.detect_category_for_text("Pillow filler") == "Pillow"
    assert amparser.detect_category_for_text("Knitted Pillow") == "Pillow"


def test_pillow_brand_comfot():
    b, _ = amparser.normalize_brand_and_size("Comfot Gusset Pillow", "43X69")
    assert b == "Comfort Gusset Pillow"
    assert _ == "43X69"


def test_tob_identity_key():
    core = {"brand": "Daisy", "size": "150x220", "product_type": "Blanket"}
    extra = {}
    key = amparser.build_item_key(
        core, extra, ["brand", "size", "product", "color"],
    )
    assert "DAISY" in key
    assert "150X220" in key
    assert "BLANKET" in key


def test_pillow_identity_key():
    core = {"brand": "Nova", "size": "43X69", "product_type": "Pillow filler"}
    key = amparser.build_item_key(
        core, {}, ["brand", "size", "product"],
    )
    assert "NOVA" in key
    assert "43X69" in key
    assert "PILLOW FILLER" in key


def test_parse_tob_sheet_if_present():
    if not TOB_SRC.exists():
        return
    import pandas as pd

    with pd.ExcelFile(TOB_SRC) as xl:
        sheet = xl.sheet_names[0]
    articles, suggested, _, needs, breakdown = amparser.parse_article_sheet(
        TOB_SRC,
        sheet,
        {
            "TOB": ["brand", "size", "product", "color"],
            "Pillow": ["brand", "size", "product"],
        },
        ["brand", "size"],
        source_filename=TOB_SRC.name,
    )
    assert suggested == "TOB"
    assert breakdown.get("TOB", 0) >= 30
    assert not needs
    sample = next(a for a in articles if a.get("brand") == "All Season Blanket")
    assert sample["category"] == "TOB"
    assert sample["size"] == "150x220" or sample["size"] == "150X220" or "150" in str(sample["size"])
    blend = (sample.get("extra_attributes") or {}).get("Blend")
    assert blend and "Polyester" in blend and "Single Ply" in blend
    assert "950" in blend
    biab = [a for a in articles if a.get("product_type") == "Bed in a Bag"]
    assert biab
    assert all(a["category"] == "TOB" for a in biab)
    # Option-count columns dropped
    for a in articles:
        extra = a.get("extra_attributes") or {}
        assert "Quality" not in extra
        assert "Ply" not in extra
        keys_l = {str(k).lower() for k in extra}
        assert "dyed / printed option" not in keys_l
        assert "option" not in keys_l


def test_parse_pillow_sheet_if_present():
    if not PILLOW_SRC.exists():
        return
    import pandas as pd

    with pd.ExcelFile(PILLOW_SRC) as xl:
        sheet = xl.sheet_names[0]
    articles, suggested, _, needs, breakdown = amparser.parse_article_sheet(
        PILLOW_SRC,
        sheet,
        {
            "TOB": ["brand", "size", "product", "color"],
            "Pillow": ["brand", "size", "product"],
        },
        ["brand", "size"],
        source_filename=PILLOW_SRC.name,
    )
    assert suggested == "Pillow"
    assert breakdown.get("Pillow", 0) == 8
    assert not needs
    nova = next(a for a in articles if a.get("brand") == "Nova")
    assert nova["category"] == "Pillow"
    assert nova["size"] == "43X69"
    extra = nova.get("extra_attributes") or {}
    assert extra.get("Blend") == "100% Polyester, 600 grm"
    assert extra.get("Print Style") == "White"
    assert extra.get("Units") in {1, "1"}
    comfort = next(a for a in articles if "Comfort" in str(a.get("brand") or ""))
    assert comfort["brand"] == "Comfort Gusset Pillow"

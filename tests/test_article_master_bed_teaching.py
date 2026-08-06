"""Bed Article Master teaching locks — size/brand dict, Color drop, display names."""

import article_master_parser as amparser


def test_size_code_dictionary():
    assert amparser.normalize_size_code("DBL BS") == "DB BS"
    assert amparser.normalize_size_code("KDB BS") == "KS BS"
    assert amparser.normalize_size_code("DB Fitted Sheet") == "DB FS"
    assert amparser.normalize_size_code("KDB Fitted Sheet") == "KB FS"
    assert amparser.normalize_size_code("Double Bedsheet") == "DB BS"
    assert amparser.normalize_size_code("King Fitted Sheet") == "KB FS"
    assert amparser.normalize_size_code(None, force_king_bs=True) == "KS BS"
    # Matching keys must treat DBL BS as DB BS (distributor spelling)
    assert amparser.normalize_key_part_value("size", "DBL BS") == "DB BS"
    assert amparser.normalize_key_part_value("size", "DB BS") == "DB BS"
    assert amparser.normalize_key_part_value("size", "DBL B.S.") == "DB BS"
    assert amparser.normalize_key_part_value("size", "DB.BS") == "DB BS"
    assert amparser.build_item_key(
        {"brand": "Cotton Comforts", "size": "DBL BS"}, {}, ["brand", "size"],
    ) == amparser.build_item_key(
        {"brand": "Cotton Comforts", "size": "DB BS"}, {}, ["brand", "size"],
    )


def test_size_display_names():
    assert amparser.size_display_name("DB BS") == "Double Bedsheet"
    assert amparser.size_display_name("SB BS") == "Single Bedsheet"
    assert amparser.size_display_name("KS BS") == "King Bedsheet"
    assert amparser.size_display_name("DB FS") == "Double Fitted Sheet"
    assert amparser.size_display_name("KB FS") == "King Fitted Sheet"
    assert amparser.size_display_name("DB Reversible Comf") == "Double Reversible Comforter"
    assert amparser.size_display_name("DBL BS") == "Double Bedsheet"


def test_product_inferred_from_comforter_size():
    assert amparser.infer_product_from_size("DB Reversible Comf") == "Comforter"
    assert amparser.infer_product_from_size("DB Comf") == "Comforter"
    assert amparser.infer_product_from_size("DB Duvet Cover") == "Duvet Cover"
    assert amparser.infer_product_from_size("DB FS") == "Fitted Sheet"
    assert amparser.resolve_product_type("Bedsheet", "DB Reversible Comf") == "Comforter"
    assert amparser.resolve_product_type("Bedsheet", "DB BS") == "Bedsheet"
    assert amparser.resolve_product_type("Blanket", "DB Comf") == "Blanket"
    assert amparser.resolve_category("Bed", "DB Reversible Comf", "Comforter") == "TOB"
    assert amparser.resolve_category("Bed", "DB Comf", "Comforter") == "TOB"
    assert amparser.resolve_category("Bed", "DB BS", "Bedsheet") == "Bed"
    assert amparser.resolve_category("Bed", "DB Comf", "Bed In Bag") == "Bed"
    assert amparser.resolve_category("Bed", "DB Duvet Cover", "Duvet Cover") == "Bed"
    assert amparser.resolve_category("Bed", "DB Duvet Cover", "Bedsheet") == "Bed"


def test_brand_king_splits_to_ks_bs():
    brand, size = amparser.normalize_brand_and_size("Cardinal KING", "DB BS")
    assert brand == "Cardinal"
    assert size == "KS BS"
    brand, size = amparser.normalize_brand_and_size("Epigram King", None)
    assert brand == "Epigram"
    assert size == "KS BS"


def test_stripe_brand_canonical():
    brand, size = amparser.normalize_brand_and_size(
        "Cotton Satin Stripe (1 Cms)", "DB BS",
    )
    assert brand == "Cotton Satin With 1 CM Stripe"
    assert size == "DB BS"


def test_wonder_land_and_binb_stay_separate():
    a, _ = amparser.normalize_brand_and_size("Wonder Land- Glow", "DB BS")
    b, _ = amparser.normalize_brand_and_size("Wonder Land- Kids", "DB BS")
    assert a != b
    c, _ = amparser.normalize_brand_and_size("Celebrating India", "DB BS")
    d, _ = amparser.normalize_brand_and_size("Celebrating India (BINB)", "DB BS")
    assert c == "Celebrating India"
    assert d == "Celebrating India (BINB)"
    assert c != d


def test_colorway_counts_excluded_sku_color_kept():
    # Booking counts still excluded
    assert amparser.is_excluded_extra_column("Colorways")
    assert amparser.is_excluded_extra_column("Colours")
    assert amparser.is_excluded_extra_column("No of Design")
    # SKU Color kept globally (Bath); Bed strips via category=
    assert not amparser.is_excluded_extra_column("Color")
    assert amparser.is_excluded_extra_column("Color", category="Bed")
    cleaned = amparser.strip_excluded_extra_attributes({
        "Color": "White",
        "Packing": "Envelope",
        "No of Design": 12,
        "Colours": 6,
    })
    assert cleaned.get("Color") == "White"
    assert cleaned["Packing"] == "Envelope"
    assert "Colours" not in cleaned
    bed_cleaned = amparser.strip_excluded_extra_attributes(
        {"Color": "White", "Packing": "Envelope"}, category="Bed",
    )
    assert "Color" not in bed_cleaned
    assert bed_cleaned["Packing"] == "Envelope"


def test_packing_latest_wins_vs_gapfill():
    existing = {"Packing": "Envelope", "Blend": "60/40"}
    older = {"Packing": "PVC Bag", "Print Style": "Pigment"}
    # Older season must not overwrite Packing; may gap-fill Print Style
    merged = amparser.merge_extra_attributes(existing, older, overwrite_nonblank=False)
    assert merged["Packing"] == "Envelope"
    assert merged["Print Style"] == "Pigment"
    # Latest season overwrites Packing
    merged2 = amparser.merge_extra_attributes(existing, older, overwrite_nonblank=True)
    assert merged2["Packing"] == "PVC Bag"


def test_percent_display():
    assert amparser.format_percent_display(0.4) == "40%"
    assert amparser.format_percent_display(40) == "40%"
    assert amparser.format_percent_display("18%") == "18%"


def test_separate_sku_sizes_not_aliased_together():
    assert amparser.normalize_size_code("DB BS") == "DB BS"
    assert amparser.normalize_size_code("DB FS") == "DB FS"
    assert amparser.normalize_size_code("DB Reversible Comf") == "DB Reversible Comf"
    assert amparser.normalize_size_code("DB Duvet Cover") == "DB Duvet Cover"
    codes = {
        amparser.normalize_size_code("DB BS"),
        amparser.normalize_size_code("DB FS"),
        amparser.normalize_size_code("DB Reversible Comf"),
        amparser.normalize_size_code("DB Duvet Cover"),
    }
    assert len(codes) == 4

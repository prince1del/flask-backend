"""Bath / Towel Article Master teaching locks."""

import article_master_parser as amparser


def test_towel_size_display_names():
    assert amparser.normalize_size_code("40x60") == "Hand Towel"
    assert amparser.normalize_size_code("75x150") == "Bath Towel"
    assert amparser.normalize_size_code("60x120") == "Ladies Towel"
    assert amparser.normalize_size_code("90x180") == "Pool Towel"
    assert amparser.normalize_size_code("R4") == "Towel Set"
    assert amparser.normalize_size_code("30x30") == "Face Towel"
    assert amparser.normalize_size_code("40x60(2pc)") == "Hand Towel Set of 2"
    assert amparser.normalize_size_code("40x60 (SET OF 2)") == "Hand Towel Set of 2"
    assert amparser.normalize_size_code("30x30(3pc)") == "Face Towel Set of 3"
    assert amparser.normalize_size_code("50x100") == "Gym Towel"
    assert amparser.normalize_size_code("91x100") == "91x100"
    assert amparser.normalize_size_code("L") == "Large"
    assert amparser.size_display_name("40x60") == "Hand Towel"
    assert amparser.size_display_name("Hand Towel") == "Hand Towel"


def test_towel_brand_spelling():
    b, _ = amparser.normalize_brand_and_size("Bd White", "40x60")
    assert b == "BD White"
    b, s = amparser.normalize_brand_and_size("Lepord", "75x150")
    assert b == "Leopard"
    assert s == "Bath Towel"
    b, _ = amparser.normalize_brand_and_size("Eco stripe", "40x60")
    assert b == "Eco Stripe"
    b, _ = amparser.normalize_brand_and_size("GYM Towel", "50x100")
    assert b == "Gym Towel"
    b, _ = amparser.normalize_brand_and_size("Flora Bathrobe", "L")
    assert b == "Flora Bathrobe"


def test_bathmat_product_and_category():
    assert amparser.normalize_product_spelling("Bathmat  (Anti skid)") == "Bathmat Antiskid"
    assert amparser.normalize_product_spelling("Bathmat") == "Bathmat Antiskid"
    assert amparser.detect_category_for_text("Bathmat") == "Bath"
    assert amparser.detect_category_for_text("Bathrobe") == "Bath"
    assert amparser.detect_category_for_text("Towelling Fabric") == "Bath"


def test_towel_color_and_pvc():
    color, packing, pkg_only = amparser.normalize_towel_color_and_packing(
        "Assorted 12 (PVC bag Pkg)", None,
    )
    assert color == "Assorted 12"
    assert packing == "PVC bag Pkg"
    assert pkg_only is False

    color, packing, pkg_only = amparser.normalize_towel_color_and_packing(
        "Assorted 12 ( L)", "FLORA … (Pkg)",
    )
    assert color == "Assorted 12"
    assert packing is None
    assert pkg_only is True

    color, packing, pkg_only = amparser.normalize_towel_color_and_packing(
        "Assorted 12", "FLORA DYED 75cm x 1.5m ASST12 (PVC Bag PKG)",
    )
    assert color == "Assorted 12"
    assert packing == "PVC bag Pkg"

    color, _, _ = amparser.normalize_towel_color_and_packing("Asst-06", None)
    assert color == "Assorted 06"
    color, _, _ = amparser.normalize_towel_color_and_packing("Assorted 9", None)
    assert color == "Assorted 09"
    color, _, _ = amparser.normalize_towel_color_and_packing("Jacquarad", None)
    assert color == "Jacquard"
    color, _, _ = amparser.normalize_towel_color_and_packing("WHITE", None)
    assert color == "White"


def test_bath_key_includes_color_product():
    core = {"brand": "Flora", "size": "Hand Towel", "product_type": "Terry Towel"}
    extra = {"Color": "White"}
    key = amparser.build_item_key(core, extra, ["brand", "size", "color", "product"])
    assert "FLORA" in key
    assert "HAND TOWEL" in key
    assert "WHITE" in key
    assert "TERRY TOWEL" in key


def test_pack_sizes_alias():
    assert amparser.resolve_core_field_for_name("Pack Sizes") == "bale_pack_size"
    assert amparser.resolve_core_field_for_name("Bale Pack Sizes") == "bale_pack_size"

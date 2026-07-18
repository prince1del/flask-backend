from centralized_db_system.order_reconciliation import (
    make_item_key,
    normalize_product_code,
    parse_material_description,
    parse_so_ci_text,
    group_document_lines,
)


def test_aliases_locked_by_founder():
    assert normalize_product_code("DBBS") == "DB BS"
    assert normalize_product_code("DBS") == "DB BS"
    assert normalize_product_code("KBS") == "KS BS"
    assert normalize_product_code("KBFS") == "KB FS"
    assert normalize_product_code("DFS") == "DB FS"


def test_material_description_parsing():
    parsed = parse_material_description("ASTER 1+2 DB SET 224X244 7985BLU 100TC")
    assert parsed["brand"] == "ASTER"
    assert parsed["product_code"] == "DB BS"
    assert parsed["bedset_size"] == "224X244"
    assert parsed["design_no"] == "7985"
    assert parsed["color_code"] == "BLU"
    assert parsed["tc"] == "100"
    assert make_item_key(parsed["brand"], parsed["tc"], parsed["product_code"]) == "ASTER|100|DB"


def test_aster_so_18_lines_group_to_one_item():
    text = """
    BS03DBASTER7985BLU ASTER 1+2 DB SET
    224X244 7985BLU 100TC
    63041910 66.000 580.00 SET 07.06.2026 38,280.00 1,914.00 40,194.00
    BS03DBASTER7985ORG ASTER 1+2 DB SET
    224X244 7985ORG 100TC
    63041910 66.000 580.00 SET 07.06.2026 38,280.00 1,914.00 40,194.00
    BS03DBASTER7985PNK ASTER 1+2 DB SET
    224X244 7985PNK 100TC
    63041910 66.000 580.00 SET 07.06.2026 38,280.00 1,914.00 40,194.00
    """
    lines = parse_so_ci_text(text)
    assert len(lines) == 3
    groups = group_document_lines(lines)
    assert len(groups) == 1
    group = next(iter(groups.values()))
    assert group.item_key == "ASTER|100|DB"
    assert group.qty == 198
    assert group.net_value == 114840
    assert group.to_dict(False)["design_count"] == 1
    assert group.to_dict(False)["colorways_per_design_count"] == 3

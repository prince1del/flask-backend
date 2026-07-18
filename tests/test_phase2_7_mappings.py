from centralized_db_system.db import CentralizedDB


def test_material_code_mappings_and_decode(tmp_path):
    db_path = str(tmp_path / "mappings.db")
    db = CentralizedDB(db_path)

    ws = "default"
    mid1 = db.add_material_code_mapping("BS03", "product_prefix", "BS03", "Bedsheet prefix", workspace_id=ws)
    mid2 = db.add_material_code_mapping("PNK", "color", "Pink", "Pink color", workspace_id=ws)

    mappings = db.list_material_code_mappings(workspace_id=ws)
    assert any(m["id"] == mid1 for m in mappings)

    decoded = db.decode_material_code("BS03KSGRDSP7847PNK")
    assert decoded.get("product_prefix") == "BS03"
    assert decoded.get("color") == "Pink"

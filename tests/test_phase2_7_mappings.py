from centralized_db_system.db import CentralizedDB


def test_material_code_mappings_and_decode(tmp_path):
    db_path = str(tmp_path / "mappings.db")
    db = CentralizedDB(db_path)

    ws = "default"
    mid1 = db.add_material_code_mapping(
        "BS03", "product_prefix", "BS03", "Bedsheet prefix", workspace_id=ws
    )
    mid2 = db.add_material_code_mapping(
        "PNK", "color", "Pink", "Pink color", workspace_id=ws
    )

    mappings = db.list_material_code_mappings(workspace_id=ws)
    assert any(m["id"] == mid1 for m in mappings)
    assert mid2

    decoded = db.decode_material_code("BS03KSGRDSP7847PNK", workspace_id=ws)
    assert decoded.get("product_prefix") == "BS03"
    assert decoded.get("color") == "Pink"


def test_decode_material_code_is_workspace_scoped(tmp_path):
    db = CentralizedDB(str(tmp_path / "mappings_iso.db"))
    db.add_material_code_mapping(
        "SECRET", "brand", "OtherBizBrand", workspace_id="ws-other"
    )
    db.add_material_code_mapping(
        "SECRET", "brand", "MyBrand", workspace_id="ws-mine"
    )

    mine = db.decode_material_code("SECRET123", workspace_id="ws-mine")
    assert mine == {"brand": "MyBrand"}

    other = db.decode_material_code("SECRET123", workspace_id="ws-other")
    assert other == {"brand": "OtherBizBrand"}

    # No cross-tenant bleed into an empty workspace
    assert db.decode_material_code("SECRET123", workspace_id="ws-empty") == {}

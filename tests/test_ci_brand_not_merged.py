"""Different CI brands must never merge — even when pack/size/TC look alike."""
from pathlib import Path
import sys

import pytest
from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from centralized_db_system.db import CentralizedDB
from order_item_keys import line_brands_match

FLORA = "FLORA DB 1+2 224x254 BLD 180TC"
COTTON = "COTTON COMFORT DB 1+2 224x254 BLD 180TC"


def test_shared_size_suffix_looks_similar_but_brands_differ():
    score = max(
        fuzz.token_set_ratio(FLORA.lower(), COTTON.lower()),
        fuzz.partial_ratio(FLORA.lower(), COTTON.lower()),
    )
    assert score >= 88
    assert not line_brands_match(FLORA, COTTON)


def test_upsert_does_not_merge_cotton_comfort_into_flora(tmp_path):
    db = CentralizedDB(str(tmp_path / "merge.sqlite3"))
    dist_id = db.add_master_distributor(name="Balaji", workspace_id="ws-1")
    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no="102875816", distributor_id=dist_id, workspace_id="ws-1"
    )
    flora = db.upsert_order_lifecycle_item(
        tracking_id=tracking_id,
        item_name=FLORA,
        source="ci",
        qty=144,
        value=80000,
        workspace_id="ws-1",
        item_key=None,
    )
    cotton = db.upsert_order_lifecycle_item(
        tracking_id=tracking_id,
        item_name=COTTON,
        source="ci",
        qty=12,
        value=14000,
        workspace_id="ws-1",
        item_key="COTTON COMFORT|180|DB",
    )
    assert flora["id"] != cotton["id"]
    assert "FLORA" in (flora["item_name"] or "").upper()
    assert "COTTON" in (cotton["item_name"] or "").upper()
    assert pytest.approx(flora["ci_qty"] or 0) == 144
    assert pytest.approx(cotton["ci_qty"] or 0) == 12
    assert pytest.approx(flora["ci_value"] or 0) == 80000
    assert pytest.approx(cotton["ci_value"] or 0) == 14000

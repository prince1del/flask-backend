"""Target vs Achievement distributor linking to master_distributors."""

import sqlite3

import pytest

from centralized_db_system.db import CentralizedDB


@pytest.fixture
def linked_ta_db(tmp_path):
    db_path = tmp_path / "ta_link.sqlite3"
    db = CentralizedDB(str(db_path))
    db.ensure_target_achievement_tables()
    db.add_master_distributor(
        "Savitri Contact",
        firm_name="Savitri Steel Cement Traders",
        firm_nick_name="Savitri Steel",
        workspace_id="ws-test",
    )
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO target_achievement_years (
                workspace_id, financial_year, target_amount, achievement_amount
            ) VALUES (?, ?, ?, ?)
            """,
            ("ws-test", "2025-2026", 100.0, 0.0),
        )
        conn.commit()
        year_id = int(cur.lastrowid)
    return db, year_id


def test_resolve_matches_excel_suffix_to_master(linked_ta_db):
    db, _year_id = linked_ta_db
    resolved = db.resolve_ta_distributor_reference(
        "Savitri Steel Cement Traders, Varan",
        "ws-test",
        "Savitri steel Varanasi",
    )
    assert resolved["matched"] is True
    assert resolved["distributor_name"] == "Savitri Steel Cement Traders"
    assert resolved["distributor_id"] is not None


def test_resolve_keeps_unknown_distributor_as_is(linked_ta_db):
    db, _year_id = linked_ta_db
    resolved = db.resolve_ta_distributor_reference(
        "Zirise Technologies Private Limited",
        "ws-test",
        "Zirise Haryana",
    )
    assert resolved["matched"] is False
    assert resolved["distributor_name"] == "Zirise Technologies Private Limited"
    assert resolved["distributor_id"] is None


def test_upsert_links_breakup_to_master(linked_ta_db):
    db, year_id = linked_ta_db
    db.upsert_target_distributor_breakup(
        workspace_id="ws-test",
        financial_year_id=year_id,
        distributor_name="Savitri Steel Cement Traders, Varan",
        achievement_lakhs=42.5,
        nick="Savitri steel Varanasi",
        source="excel_upload",
    )
    rows = db.list_target_distributor_breakup("ws-test", year_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["matched"] is True
    assert row["distributor_name"] == "Savitri Steel Cement Traders"
    assert row["source_distributor_name"] == "Savitri Steel Cement Traders, Varan"
    assert row["achievement_lakhs"] == 42.5
    assert row["achievement_excel"] == 42.5
    assert row["achievement_manual"] == 0.0


def test_ci_upsert_does_not_overwrite_manual_achievement(linked_ta_db):
    db, year_id = linked_ta_db
    db.upsert_target_distributor_breakup(
        workspace_id="ws-test",
        financial_year_id=year_id,
        distributor_name="Savitri Steel Cement Traders",
        achievement_lakhs=10.0,
        target_lakhs=50.0,
        source="manual",
    )
    db.upsert_target_distributor_breakup(
        workspace_id="ws-test",
        financial_year_id=year_id,
        distributor_name="Savitri Steel Cement Traders",
        achievement_lakhs=3.5,
        source="ci",
    )
    rows = db.list_target_distributor_breakup("ws-test", year_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["achievement_manual"] == 10.0
    assert row["achievement_ci"] == 3.5
    assert row["achievement_excel"] == 0.0
    assert row["achievement_lakhs"] == 13.5
    assert row["target_lakhs"] == 50.0


def test_manual_category_catalog_persists_and_custom_can_be_added(linked_ta_db):
    db, _year_id = linked_ta_db
    catalog = db.ensure_manual_category_catalog("ws-test", 1)
    names = [c["name"] for c in catalog]
    assert names[:4] == ["Bed", "Bath", "TOB", "Pillow"]
    rugs = db.add_manual_category("ws-test", 1, "Rugs")
    assert rugs["name"] == "Rugs"
    assert rugs["builtin"] is False
    again = db.ensure_manual_category_catalog("ws-test", 1)
    assert "Rugs" in [c["name"] for c in again]
    # Same custom on a second FY still in catalog (year-independent).
    assert db.add_manual_category("ws-test", 1, "rugs")["name"] == "Rugs"


def test_manual_category_can_be_removed_and_restored(linked_ta_db):
    db, year_id = linked_ta_db
    assert db.remove_manual_category("ws-test", 1, "Bath") is True
    catalog = db.ensure_manual_category_catalog("ws-test", 1)
    assert "Bath" not in [c["name"] for c in catalog]
    # Re-adding a hidden builtin restores it without duplicating rows.
    restored = db.add_manual_category("ws-test", 1, "Bath")
    assert restored["name"] == "Bath"
    again = db.ensure_manual_category_catalog("ws-test", 1)
    assert "Bath" in [c["name"] for c in again]
    db.replace_distributor_manual_categories(
        workspace_id="ws-test",
        user_id=1,
        financial_year_id=year_id,
        distributor_name="Savitri Steel Cement Traders",
        categories=[{"name": "Pillow", "amount_rupees": 200_000}],
    )
    assert db.remove_manual_category("ws-test", 1, "Pillow") is True
    amounts = db.list_manual_category_amounts("ws-test", 1, year_id)
    assert all(
        c["name"].lower() != "pillow"
        for cats in amounts.values()
        for c in cats
    )


def test_manual_category_amounts_replace_on_existing_distributor(linked_ta_db):
    db, year_id = linked_ta_db
    db.upsert_target_distributor_breakup(
        workspace_id="ws-test",
        financial_year_id=year_id,
        distributor_name="Savitri Steel Cement Traders",
        achievement_lakhs=12.0,
        target_lakhs=50.0,
        source="manual",
    )
    saved = db.replace_distributor_manual_categories(
        workspace_id="ws-test",
        user_id=1,
        financial_year_id=year_id,
        distributor_name="Savitri Steel Cement Traders",
        categories=[
            {"name": "Bed", "amount_rupees": 800_000},
            {"name": "Bath", "amount_rupees": 400_000},
        ],
    )
    assert len(saved) == 2
    by_name = {c["name"]: c["amount_lakhs"] for c in saved}
    assert by_name["Bed"] == 8.0
    assert by_name["Bath"] == 4.0
    # Update existing card — replace split, keep catalog.
    saved2 = db.replace_distributor_manual_categories(
        workspace_id="ws-test",
        user_id=1,
        financial_year_id=year_id,
        distributor_name="Savitri Steel Cement Traders",
        categories=[{"name": "Bed", "amount_rupees": 1_000_000}],
    )
    assert len(saved2) == 1
    assert saved2[0]["name"] == "Bed"
    amounts = db.list_manual_category_amounts("ws-test", 1, year_id)
    dist = amounts["savitri steel cement traders"]
    assert len(dist) == 1
    catalog = db.ensure_manual_category_catalog("ws-test", 1)
    assert [c["name"] for c in catalog][:4] == ["Bed", "Bath", "TOB", "Pillow"]

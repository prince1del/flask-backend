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

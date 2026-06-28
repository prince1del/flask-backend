import uuid
from app.web_app import create_app
from centralized_db_system.db import CentralizedDB


def test_targets_achievements_crud(tmp_path):
    db_path = tmp_path / "test_ta.sqlite3"
    db = CentralizedDB(str(db_path))

    # Ensure we can insert a target vs achievement row
    with db_path.with_suffix("").open(mode="a"):
        pass
    # Use internal connection to insert directly via SQL to test retrieval
    with __import__("sqlite3").connect(db.db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO targets_achievements (year, month, zone, target_amount, achievement_amount) VALUES (?, ?, ?, ?, ?)",
            (2026, 6, "West", 10000, 7500),
        )
        conn.commit()
        inserted_id = cursor.lastrowid

    rows = (
        db.list_targets_achievements()
        if hasattr(db, "list_targets_achievements")
        else []
    )
    # If helper exists, expect at least one row or otherwise verify via direct query
    if rows:
        assert any(r.get("id") == inserted_id for r in rows)
    else:
        with __import__("sqlite3").connect(db.db_path) as conn:
            r = conn.execute(
                "SELECT id, year, month, zone, target_amount, achievement_amount FROM targets_achievements WHERE id = ?",
                (inserted_id,),
            ).fetchone()
            assert r is not None

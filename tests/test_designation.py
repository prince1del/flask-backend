"""Designation (job title) on the profile: saved, returned, trimmed; empty clears it."""
from __future__ import annotations

from centralized_db_system.db import CentralizedDB


def test_designation_round_trip(tmp_path):
    db = CentralizedDB(str(tmp_path / "des.sqlite3"))
    db.create_user("asha", "StrongPass123")
    uid = next(u["id"] for u in [db.get_user_profile(i) for i in range(1, 5)] if u and u["username"] == "asha")
    out = db.update_user_profile(uid, designation="  Area Sales Manager  ")
    assert out["designation"] == "Area Sales Manager"
    assert db.get_user_profile(uid)["designation"] == "Area Sales Manager"
    assert db.update_user_profile(uid, designation="")["designation"] is None

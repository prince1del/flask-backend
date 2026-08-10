"""Party merge reverse must restore only aliases moved at merge time."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3

os.environ.setdefault("SECRET_KEY", "test-merge-reverse-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-merge-reverse-secret")
os.environ["AUTH_ENABLED"] = "true"

from centralized_db_system.db import CentralizedDB


def _setup(tmp_path, monkeypatch):
    db_path = tmp_path / "party_merge_reverse.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "test-merge-reverse-secret")
        monkeypatch.setenv("JWT_SECRET_KEY", "test-merge-reverse-secret")

    _apply_env()
    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply_env()

    app = web_app_module.create_app()
    app.config.update(TESTING=True, DATABASE_PATH=str(db_path))
    CentralizedDB(str(db_path)).create_user(
        "merge_admin", "pass123", role="admin", workspace_id="default"
    )
    client = app.test_client()
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "merge_admin", "password": "pass123"},
    )
    assert login.status_code == 200, login.get_data(as_text=True)
    token = login.get_json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return client, headers, str(db_path)


def _seed_merge_fixture(db_path: str):
    source = "src-party-uuid"
    target = "tgt-party-uuid"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO master_parties
            (party_uuid, party_type, workspace_id, primary_name, status)
            VALUES (?, 'distributor', 'default', 'Source Co', 'active')
            """,
            (source,),
        )
        conn.execute(
            """
            INSERT INTO master_parties
            (party_uuid, party_type, workspace_id, primary_name, status)
            VALUES (?, 'distributor', 'default', 'Target Co', 'active')
            """,
            (target,),
        )
        # Target's own alias — must stay on target after reverse
        conn.execute(
            """
            INSERT INTO party_aliases (party_uuid, workspace_id, alias_name, source)
            VALUES (?, 'default', 'Target Own Alias', 'manual')
            """,
            (target,),
        )
        # Source aliases — move to target on merge, return on reverse
        conn.execute(
            """
            INSERT INTO party_aliases (party_uuid, workspace_id, alias_name, source)
            VALUES (?, 'default', 'Source Alias A', 'manual')
            """,
            (source,),
        )
        conn.execute(
            """
            INSERT INTO party_aliases (party_uuid, workspace_id, alias_name, source)
            VALUES (?, 'default', 'Source Alias B', 'manual')
            """,
            (source,),
        )
        conn.execute(
            """
            INSERT INTO party_matching_history
            (workspace_id, party1_uuid, party1_name, party2_uuid, party2_name,
             final_confidence_score, match_category, suggested_action, source)
            VALUES ('default', ?, 'Source Co', ?, 'Target Co', 95.0, 'duplicate', 'merge', 'test')
            """,
            (source, target),
        )
        match_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        target_own = conn.execute(
            "SELECT alias_id FROM party_aliases WHERE alias_name = 'Target Own Alias'"
        ).fetchone()[0]
        source_a = conn.execute(
            "SELECT alias_id FROM party_aliases WHERE alias_name = 'Source Alias A'"
        ).fetchone()[0]
        source_b = conn.execute(
            "SELECT alias_id FROM party_aliases WHERE alias_name = 'Source Alias B'"
        ).fetchone()[0]
        return {
            "match_id": match_id,
            "source": source,
            "target": target,
            "target_own": int(target_own),
            "source_a": int(source_a),
            "source_b": int(source_b),
        }
    finally:
        conn.close()


def test_reverse_merge_keeps_target_original_aliases(tmp_path, monkeypatch):
    client, headers, db_path = _setup(tmp_path, monkeypatch)
    fixture = _seed_merge_fixture(db_path)

    approve = client.post(
        "/api/v1/party-matching/approve-merge",
        headers=headers,
        json={"match_id": fixture["match_id"]},
    )
    assert approve.status_code == 200, approve.get_data(as_text=True)
    body = approve.get_json()["data"]
    merge_id = body["merge_id"]
    assert set(body["moved_alias_ids"]) == {fixture["source_a"], fixture["source_b"]}

    conn = sqlite3.connect(db_path)
    try:
        owners = {
            int(r[0]): r[1]
            for r in conn.execute("SELECT alias_id, party_uuid FROM party_aliases")
        }
        assert owners[fixture["source_a"]] == fixture["target"]
        assert owners[fixture["source_b"]] == fixture["target"]
        assert owners[fixture["target_own"]] == fixture["target"]
        notes = conn.execute(
            "SELECT notes FROM party_merges WHERE id = ?", (merge_id,)
        ).fetchone()[0]
        assert set(json.loads(notes)["moved_alias_ids"]) == {
            fixture["source_a"],
            fixture["source_b"],
        }
    finally:
        conn.close()

    reverse = client.post(
        "/api/v1/party-matching/reverse-merge",
        headers=headers,
        json={"merge_id": merge_id},
    )
    assert reverse.status_code == 200, reverse.get_data(as_text=True)
    assert reverse.get_json()["data"]["aliases_restored"] == 2

    conn = sqlite3.connect(db_path)
    try:
        owners = {
            int(r[0]): r[1]
            for r in conn.execute("SELECT alias_id, party_uuid FROM party_aliases")
        }
        # Source aliases restored; target's original alias untouched
        assert owners[fixture["source_a"]] == fixture["source"]
        assert owners[fixture["source_b"]] == fixture["source"]
        assert owners[fixture["target_own"]] == fixture["target"]
        status = conn.execute(
            "SELECT status FROM master_parties WHERE party_uuid = ?",
            (fixture["source"],),
        ).fetchone()[0]
        assert status == "active"
    finally:
        conn.close()


def test_legacy_reverse_without_snapshot_does_not_steal_target_aliases(
    tmp_path, monkeypatch
):
    client, headers, db_path = _setup(tmp_path, monkeypatch)
    fixture = _seed_merge_fixture(db_path)

    # Simulate old buggy merge: aliases already on target, notes empty
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE party_aliases SET party_uuid = ? WHERE party_uuid = ?",
            (fixture["target"], fixture["source"]),
        )
        conn.execute(
            "UPDATE master_parties SET status = 'merged' WHERE party_uuid = ?",
            (fixture["source"],),
        )
        conn.execute(
            """
            INSERT INTO party_merges
            (workspace_id, source_party_uuid, target_party_uuid, merge_status, can_reverse, notes)
            VALUES ('default', ?, ?, 'approved', 1, NULL)
            """,
            (fixture["source"], fixture["target"]),
        )
        merge_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    reverse = client.post(
        "/api/v1/party-matching/reverse-merge",
        headers=headers,
        json={"merge_id": merge_id},
    )
    assert reverse.status_code == 200, reverse.get_data(as_text=True)
    data = reverse.get_json()["data"]
    assert data["aliases_restored"] == 0
    assert "warning" in data

    conn = sqlite3.connect(db_path)
    try:
        owners = {
            int(r[0]): r[1]
            for r in conn.execute("SELECT alias_id, party_uuid FROM party_aliases")
        }
        # Must not steal target_own (or source aliases still on target) via bulk move
        assert owners[fixture["target_own"]] == fixture["target"]
        assert owners[fixture["source_a"]] == fixture["target"]
        assert owners[fixture["source_b"]] == fixture["target"]
    finally:
        conn.close()

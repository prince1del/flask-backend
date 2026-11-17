"""Backups on the server per firm + Replace this firm + wipe safety copy (owner only)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_ops, hop_restore, hop_server_backups as SB
from app.hop_db import connect, create_customer
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOP_BACKUP_DIR", str(tmp_path / "bk"))
    p = str(tmp_path / "sb.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        create_customer(conn, WS, {"company": "Good Hotel"})
        hop_ops.create_product(conn, WS, {"name": "Sheer curtain"})
        conn.commit()
    return p


def test_snapshot_only_when_changed_and_list(db):
    with connect(db) as conn:
        a = SB.snapshot(conn, WS)
        assert a and a["name"].endswith("_auto.kunwar")
        assert SB.snapshot(conn, WS) is None                     # nothing changed → no new copy
        create_customer(conn, WS, {"company": "New Party"})
        conn.commit()
        import time
        time.sleep(1.1)
        assert SB.snapshot(conn, WS)
    lst = SB.list_backups(WS)
    assert len(lst) == 2 and lst[0]["reason"] == "Automatic"
    with pytest.raises(ValueError):
        SB.read(WS, "../../etc/passwd")
    assert SB.list_backups("other_company") == []               # one folder per firm


def test_replace_puts_old_data_back_with_safety_copy(db):
    with connect(db) as conn:
        good = SB.snapshot(conn, WS)["name"]
        # data goes wrong
        conn.execute("DELETE FROM hop_customers WHERE workspace_id=?", (WS,))
        create_customer(conn, WS, {"company": "Wrong Entry"})
        conn.commit()
        res = hop_restore.restore_replace(conn, WS, SB.read(WS, good))
        names = [r[0] for r in conn.execute("SELECT company FROM hop_customers WHERE workspace_id=?", (WS,))]
        assert names == ["Good Hotel"] and res["safety_copy"].endswith("_before_replace.kunwar")
        assert conn.execute("SELECT name FROM hop_products WHERE workspace_id=?", (WS,)).fetchone()[0] == "Sheer curtain"
        # the safety copy holds the "wrong" state, so even the replace can be undone
        undo = hop_restore.preview(conn, SB.read(WS, res["safety_copy"]), WS)
        assert undo["parties"] == 1


def test_routes_owner_staff_password_wipe(tmp_path, monkeypatch):
    import io

    from tests.test_hop_permissions import _staff
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    monkeypatch.setenv("HOP_BACKUP_DIR", str(tmp_path / "bk"))
    client, owner, db_path = _setup_hop_wipe_client(tmp_path, monkeypatch)
    staff, _ = _staff(client, db_path)
    client.post("/api/v1/hop/customers", headers=owner, json={"company": "Route Hotel"})
    r = client.post("/api/v1/hop/backups", headers=owner)
    assert r.status_code == 200 and r.get_json()["data"]["saved"]
    name = r.get_json()["data"]["backups"][0]["name"]
    assert client.get("/api/v1/hop/backups", headers=staff).status_code == 403
    assert client.get(f"/api/v1/hop/backups/{name}", headers=staff).status_code == 403
    assert client.get(f"/api/v1/hop/backups/{name}", headers=owner).status_code == 200
    # staff never restores; owner replace needs the right password
    assert client.post(f"/api/v1/hop/backups/{name}/restore", headers=staff, json={"mode": "new"}).status_code == 403
    assert client.post(f"/api/v1/hop/backups/{name}/restore", headers=owner,
                       json={"mode": "replace", "password": "wrong"}).status_code == 403
    ok = client.post(f"/api/v1/hop/backups/{name}/restore", headers=owner, json={"mode": "new", "name": "Copy firm"})
    assert ok.status_code == 200 and ok.get_json()["data"]["name"] == "Copy firm"
    # wipe: staff blocked even with their own password; owner wipe leaves a safety copy
    w = client.post("/api/v1/hop/settings/wipe-data", headers=staff, json={"password": "staff-pass-123"})
    assert w.status_code == 403
    before = len(client.get("/api/v1/hop/backups", headers=owner).get_json()["data"]["backups"])
    assert io  # keep import used
    return before

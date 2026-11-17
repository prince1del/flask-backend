"""Restore a NEXORA backup into a NEW firm: same data, new ids, links intact, original untouched."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_backup, hop_ops, hop_restore
from app.hop_db import connect, create_customer
from app.hop_doc_preview import build_txn_preview, create_manual_party_document as create
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def shop(tmp_path: Path):
    p = str(tmp_path / "rs.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        c = create_customer(conn, WS, {"company": "Hotel Blue", "mobile": "9876543210"})
        pr = hop_ops.create_product(conn, WS, {"name": "Blackout curtain", "selling_price": 1200, "stock_qty": 20})
        bill = create(conn, WS, {"txn_type": 1, "customer_id": c["id"], "txn_date": "2026-11-01",
                                 "lines": [{"item_name": "Blackout curtain", "product_id": pr["id"], "qty": 2, "rate": 1200}]})
        conn.execute("INSERT INTO hop_firm_profile (workspace_id, firm_name, updated_at) VALUES (?, 'Blue Decor', '2026') "
                     "ON CONFLICT(workspace_id) DO UPDATE SET firm_name='Blue Decor'", (WS,))
        conn.commit()
        data, _ = hop_backup.build_zip(conn, WS)
    return p, data, bill


def test_preview_and_restore(shop):
    p, data, bill = shop
    with connect(p) as conn:
        pv = hop_restore.preview(conn, data)
        assert pv["firm_name"] == "Blue Decor" and pv["parties"] == 1 and pv["items"] == 1 and pv["documents"] == 1
        before = conn.execute("SELECT COUNT(*) FROM hop_party_transactions WHERE workspace_id=?", (WS,)).fetchone()[0]
        r = hop_restore.restore_into_new_firm(conn, WS, data, "Blue Decor (restored)")
        nw = r["firm_ws"]
        assert nw != WS and r["documents"] == 1 and r["items"] == 1 and r["parties"] == 1
        # original untouched
        assert conn.execute("SELECT COUNT(*) FROM hop_party_transactions WHERE workspace_id=?", (WS,)).fetchone()[0] == before
        # new firm: bill with its line, party and item linked by the new ids
        t = conn.execute("SELECT id, party_id, total_amount FROM hop_party_transactions WHERE workspace_id=?", (nw,)).fetchone()
        assert t["id"] != bill["party_txn_id"]
        party = conn.execute("SELECT company FROM hop_customers WHERE workspace_id=? AND id=?", (nw, t["party_id"])).fetchone()
        assert party["company"] == "Hotel Blue"
        prev = build_txn_preview(conn, nw, party_txn_id=t["id"])
        assert prev["lines"][0]["item_name"] == "Blackout curtain" and prev["totals"]["grand_total"] == pytest.approx(t["total_amount"])
        ln = conn.execute("SELECT product_id FROM hop_txn_lines WHERE workspace_id=?", (nw,)).fetchone()
        assert conn.execute("SELECT name FROM hop_products WHERE workspace_id=? AND id=?", (nw, ln[0])).fetchone()[0] == "Blackout curtain"
        assert conn.execute("SELECT firm_name FROM hop_firm_profile WHERE workspace_id=?", (nw,)).fetchone()[0] == "Blue Decor (restored)"
        # restoring again makes another firm, never mixes into the first
        r2 = hop_restore.restore_into_new_firm(conn, WS, data, "Blue Decor copy 2")
        assert r2["firm_ws"] not in (WS, nw)


def test_bad_files(shop):
    p, _, _ = shop
    with connect(p) as conn:
        with pytest.raises(ValueError):
            hop_restore.preview(conn, b"not a zip")
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("hello.txt", "x")
        with pytest.raises(ValueError):
            hop_restore.preview(conn, buf.getvalue())


def test_routes(tmp_path, monkeypatch):
    import io

    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    z = client.get("/api/v1/hop/backup", headers=headers)
    if z.status_code != 200:
        pytest.skip("backup route differs")
    data = z.data
    r = client.post("/api/v1/hop/firms/restore?preview=1", headers=headers,
                    data={"backup_file": (io.BytesIO(data), "b.zip")}, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    r = client.post("/api/v1/hop/firms/restore", headers=headers,
                    data={"backup_file": (io.BytesIO(data), "b.zip"), "name": "Restored test"},
                    content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["data"]["name"] == "Restored test"
    bad = client.post("/api/v1/hop/firms/restore", headers=headers,
                      data={"backup_file": (io.BytesIO(b"junk"), "b.zip")}, content_type="multipart/form-data")
    assert bad.status_code == 400


def test_locked_backup_opens_only_in_nexora_for_same_company(shop):
    import zipfile, io as _io

    from app import hop_backup

    p, data, _ = shop
    with connect(p) as conn:
        locked, _ = hop_backup.build_locked(conn, WS)
    assert hop_backup.is_locked(locked) and hop_backup.file_name("2026-11-17").endswith(".kunwar")
    with pytest.raises(zipfile.BadZipFile):          # not a zip for any other software
        zipfile.ZipFile(_io.BytesIO(locked))
    assert b"Hotel Blue" not in locked                # no readable data inside
    with connect(p) as conn:
        assert hop_restore.preview(conn, locked, WS)["parties"] == 1
        r = hop_restore.restore_into_new_firm(conn, WS, locked, "From locked")
        assert r["documents"] == 1
        with pytest.raises(ValueError, match="another company"):
            hop_restore.preview(conn, locked, "someone_else_ws")
        broken = locked[:-5] + b"xxxxx"
        with pytest.raises(ValueError):
            hop_restore.preview(conn, broken, WS)


def test_backup_only_owner_and_only_this_firm(tmp_path, monkeypatch):
    from app import hop_backup, hop_permissions
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    assert client.get("/api/v1/hop/backup", headers=headers).status_code == 200
    monkeypatch.setattr(hop_permissions, "is_owner", lambda conn, user: False)
    r = client.get("/api/v1/hop/backup", headers=headers)
    assert r.status_code == 403
    r = client.post("/api/v1/hop/firms/restore", headers=headers, data={}, content_type="multipart/form-data")
    assert r.status_code in (400, 403)


def test_backup_has_no_other_company_rows(shop):
    import csv as _csv, io as _io, zipfile as _zip

    from app import hop_backup

    p, _, _ = shop
    with connect(p) as conn:
        create_customer(conn, "other_company", {"company": "Secret Other Co"})
        conn.commit()
        data, _ = hop_backup.build_zip(conn, WS)
    z = _zip.ZipFile(_io.BytesIO(data))
    for n in z.namelist():
        if n.endswith(".csv"):
            rows = list(_csv.DictReader(_io.StringIO(z.read(n).decode())))
            assert all(r.get("workspace_id") == WS for r in rows), n
            assert "Secret Other Co" not in z.read(n).decode()


def test_restore_touches_only_the_new_firm(shop):
    p, data, _ = shop
    with connect(p) as conn:
        create_customer(conn, "other_company", {"company": "Other Co"})
        conn.commit()
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]

        def snapshot():
            out = {}
            for t in tables:
                cols = [c[1] for c in conn.execute(f"PRAGMA table_info({t})")]
                if "workspace_id" in cols:
                    out[t] = conn.execute(f"SELECT COUNT(*) FROM {t} WHERE workspace_id NOT LIKE ?",
                                          (WS + "__f%",)).fetchone()[0]
                else:
                    out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            return out

        before = snapshot()
        r = hop_restore.restore_into_new_firm(conn, WS, data, "Only mine")
        after = snapshot()
        changed = {t for t in tables if before[t] != after[t]}
        # outside the new firm only the firm list itself grows by one row
        assert changed <= {"hop_firms", "sqlite_sequence"}, changed   # sqlite_sequence = SQLite id counter
        assert all(t.startswith("hop_") for t in r["counts"])

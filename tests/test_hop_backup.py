"""HoP backup zip: this company's rows only (made-up data)."""
from __future__ import annotations

import io
import zipfile


def test_backup_zip(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, headers, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    client.post("/api/v1/hop/customers", headers=headers, json={"company": "Backup Hotel"})
    r = client.get("/api/v1/hop/backup", headers=headers)
    assert r.status_code == 200 and r.mimetype == "application/octet-stream"
    assert ".kunwar" in r.headers.get("Content-Disposition", "")
    from app import hop_backup
    from app.hop_schema import HOP_WORKSPACE_ID

    z = zipfile.ZipFile(io.BytesIO(hop_backup.unlock(r.data, HOP_WORKSPACE_ID)))
    names = z.namelist()
    assert "hop_customers.csv" in names and "README.txt" in names
    assert "Backup Hotel" in z.read("hop_customers.csv").decode()

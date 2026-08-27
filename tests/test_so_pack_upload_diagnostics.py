"""SO pack uploads must diagnose themselves — no user has to send us a file.

The Bernina case was only solvable because that user still had his ZIP. These
tests cover what a *different* user now gets: a plain-language reason, a stored
diagnostic record, the source file kept in the existing recycle area, honest
reporting of partially readable packs, per-user isolation, and no noise at all
from a healthy upload.
"""

from __future__ import annotations

import importlib
import io
import sqlite3
import zipfile

import pytest
from PIL import Image


def _scanned_pdf_bytes(pages: int = 1) -> bytes:
    """An image-only PDF — exactly what a scan or a phone photo produces."""
    imgs = [
        Image.new("RGB", (600, 850), (250, 250, 250)) for _ in range(max(1, pages))
    ]
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def _zip_of(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, raw in entries.items():
            zf.writestr(name, raw)
    return buf.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "so_diag.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "so-diag-test-key")
    monkeypatch.setenv("WORKSPACE_OWNER_USERNAME", "kunwar1del")

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)

    app = web_app_module.create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(str(db_path))
    for name in ("bd_one", "bd_two"):
        db.create_user(name, "pass123", role="sales_executive", workspace_id="ws-1")

    def login(username: str) -> dict:
        resp = client.post(
            "/api/v1/auth/login", json={"username": username, "password": "pass123"}
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        return {
            "Authorization": f"Bearer {resp.get_json()['data']['access_token']}"
        }

    return {
        "client": client,
        "login": login,
        "db": db,
        "db_path": str(db_path),
    }


def _analyze(client, headers, filename: str, raw: bytes):
    return client.post(
        "/api/v1/order-fulfillment/so-pack/analyze",
        data={"file": (io.BytesIO(raw), filename)},
        headers=headers,
        content_type="multipart/form-data",
    )


def _diag_rows(db_path: str, user_id: int | None = None) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        sql = (
            "SELECT id, user_id, outcome, source_filename, report_json, kept_file_path "
            "FROM so_pack_upload_diagnostics"
        )
        params: tuple = ()
        if user_id is not None:
            sql += " WHERE user_id = ?"
            params = (user_id,)
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def test_scanned_pack_tells_the_user_why_and_leaves_a_record(env):
    headers = env["login"]("bd_one")
    raw = _zip_of(
        {
            "SO 100200.pdf": _scanned_pdf_bytes(2),
            "SO 100201.pdf": _scanned_pdf_bytes(1),
        }
    )
    resp = _analyze(env["client"], headers, "bernina_pack.zip", raw)
    assert resp.status_code == 400, resp.get_data(as_text=True)
    err = resp.get_json()["error"]
    assert err["code"] == "so_pack_scanned_pdfs"
    text = err["message"].lower()
    assert "scanned" in text or "photograph" in text
    assert "original pdf" in text

    rows = _diag_rows(env["db_path"])
    assert len(rows) == 1, "one diagnostic record per failed upload"
    _id, _uid, outcome, source, report_json, kept = rows[0]
    assert outcome == "so_pack_scanned_pdfs"
    assert source == "bernina_pack.zip"

    import json

    report = json.loads(report_json)
    reports = report["file_reports"]
    assert len(reports) == 2
    assert {r["source_pdf"] for r in reports} == {"SO 100200.pdf", "SO 100201.pdf"}
    for r in reports:
        assert r["reason"] == "no_text_layer", r
        assert r["pages"] >= 1
        assert r["images"] >= 1
        assert r["text_chars"] < 20
        assert r["bytes"] > 0
    assert report["contents"]["container"] == "zip"
    assert report["contents"]["pdf_entries"] == 2

    # Requirement 3: the file itself is kept for support, in the recycle area.
    assert kept, "source pack must be kept, not discarded"
    from pathlib import Path

    assert Path(kept).exists()
    assert "_nexora_recycle" in kept


def test_zip_without_any_sales_order_file_says_exactly_that(env):
    headers = env["login"]("bd_one")
    raw = _zip_of({"notes.txt": b"hello", "photo.jpg": b"\xff\xd8\xff\xd9"})
    resp = _analyze(env["client"], headers, "wrong_pack.zip", raw)
    assert resp.status_code == 400, resp.get_data(as_text=True)
    body = resp.get_json()["error"]
    # The old parser error is still fine here, but it must be explicit.
    assert "no pdf" in body["message"].lower() or body["code"] == "so_pack_no_files"


def test_healthy_pack_writes_no_diagnostic_noise(env):
    from pathlib import Path

    real = Path(
        r"g:\My Drive\2026-2027\Oder Management\AW26 order\Bedsheet\SO AW 26"
        r"\Bernina\fwdrfa0385bedsheetordersjun26forcashpayment1.zip"
    )
    if not real.exists():
        pytest.skip("real readable SO pack not present on this machine")
    headers = env["login"]("bd_one")
    resp = _analyze(env["client"], headers, real.name, real.read_bytes())
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()["data"]
    assert (data["meta"].get("upload_diagnosis")) is None
    assert data["meta"]["files_parsed"] == data["meta"]["files_total"] == 27
    assert _diag_rows(env["db_path"]) == []


def test_partly_readable_pack_names_the_sales_orders_that_failed(env):
    """A mixed pack must report the unreadable SOs instead of hiding them."""
    from pathlib import Path

    real = Path(
        r"g:\My Drive\2026-2027\Oder Management\AW26 order\Bedsheet\SO AW 26"
        r"\Bernina\fwdrfa0385bedsheetordersjun26forcashpayment1.zip"
    )
    if not real.exists():
        pytest.skip("real readable SO pack not present on this machine")

    # One good SO PDF from his pack + two scans of the kind a phone produces.
    with zipfile.ZipFile(io.BytesIO(real.read_bytes())) as zf:
        good_name = zf.namelist()[0]
        good = zf.read(good_name)
    raw = _zip_of(
        {
            good_name: good,
            "SO 900001.pdf": _scanned_pdf_bytes(1),
            "SO 900002.pdf": _scanned_pdf_bytes(1),
        }
    )

    headers = env["login"]("bd_one")
    resp = _analyze(env["client"], headers, "mixed_pack.zip", raw)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    meta = resp.get_json()["data"]["meta"]
    diagnosis = meta.get("upload_diagnosis")
    assert diagnosis, "a partial parse must be reported, not silent"
    assert diagnosis["outcome"] == "so_pack_partial"
    assert diagnosis["params"]["files_total"] == 3
    assert diagnosis["params"]["files_parsed"] == 1
    assert diagnosis["params"]["files_failed"] == 2
    failed_files = {f["file"] for f in diagnosis["failed"]}
    assert failed_files == {"SO 900001.pdf", "SO 900002.pdf"}
    assert "could not be read" in diagnosis["message"]

    rows = _diag_rows(env["db_path"])
    assert len(rows) == 1
    assert rows[0][2] == "so_pack_partial"


def test_diagnostics_are_per_user_and_workspace_visible_only_to_the_owner(env):
    raw = _zip_of({"SO 100200.pdf": _scanned_pdf_bytes(1)})

    one = env["login"]("bd_one")
    two = env["login"]("bd_two")
    assert _analyze(env["client"], one, "one_pack.zip", raw).status_code == 400
    assert _analyze(env["client"], two, "two_pack.zip", raw).status_code == 400

    def listed(headers):
        resp = env["client"].get(
            "/api/v1/order-fulfillment/so-pack/upload-diagnostics", headers=headers
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        return resp.get_json()["data"]

    mine = listed(one)
    assert mine["scope"] == "mine"
    assert {r["source_filename"] for r in mine["records"]} == {"one_pack.zip"}

    theirs = listed(two)
    assert {r["source_filename"] for r in theirs["records"]} == {"two_pack.zip"}

    # The workspace owner fields these complaints, so he sees the workspace.
    env["db"].create_user(
        "kunwar1del", "pass123", role="sales_executive", workspace_id="ws-1"
    )
    conn = sqlite3.connect(env["db_path"])
    try:
        conn.execute(
            "UPDATE users SET is_workspace_owner = 1 WHERE username = 'kunwar1del'"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pytest.skip("workspace owner flag not present in this schema")
    finally:
        conn.close()

    owner = listed(env["login"]("kunwar1del"))
    assert owner["scope"] == "workspace"
    assert {r["source_filename"] for r in owner["records"]} == {
        "one_pack.zip",
        "two_pack.zip",
    }


def test_match_upload_refuses_empty_pack_with_a_reason_not_a_quiet_success(env):
    """The FO match route must also explain, never save an empty SO side."""
    headers = env["login"]("bd_one")
    import filled_orders_db as fodb

    conn = sqlite3.connect(env["db_path"])
    fodb.ensure_schema(conn)
    user_id = int(
        conn.execute(
            "SELECT id FROM users WHERE username = 'bd_one'"
        ).fetchone()[0]
    )
    distributor_id = env["db"].add_master_distributor(
        "Bernina International P Ltd", firm_nick_name="BND", workspace_id="ws-1"
    )
    fo_id = fodb.create_filled_order(
        conn,
        user_id,
        distributor_id,
        "Bernina International P Ltd",
        "Bedsheet",
        "AW26",
        source_filename="BND.xlsx",
    )
    fodb.insert_filled_order_item(
        conn,
        fo_id,
        {
            "item_key": "BLUMEN|DOUBLE BEDSHEET",
            "brand": "Blumen",
            "size": "Double Bedsheet",
            "product_type": "Double Bedsheet",
            "raw_qty_value": 36,
            "detected_unit": "pieces",
            "final_piece_qty": 36,
            "is_clean_bale_multiple": True,
            "matched": True,
            "ex_mill_price": 733.9,
        },
    )
    conn.commit()
    conn.close()

    resp = env["client"].post(
        "/api/v1/order-fulfillment/so-pack/match-filled-order",
        json={
            "filled_order_id": fo_id,
            "so_pack": {
                "meta": {
                    "source_filename": "scanned_pack.zip",
                    "files_total": 1,
                    "files_parsed": 0,
                    "file_reports": [
                        {
                            "source_pdf": "SO 100200.pdf",
                            "pages": 1,
                            "images": 1,
                            "text_chars": 0,
                            "lines": 0,
                            "reason": "no_text_layer",
                        }
                    ],
                },
                "line_detail": [],
                "consolidated": [{"so_number": "100200", "total_qty": 100}],
            },
        },
        headers=headers,
    )
    assert resp.status_code == 400, resp.get_data(as_text=True)
    err = resp.get_json()["error"]
    assert err["code"] == "so_pack_scanned_pdfs"
    assert "scanned" in err["message"].lower()
    assert err["diagnosis"]["failed"][0]["reason"] == "no_text_layer"
    assert _diag_rows(env["db_path"])[0][2] == "so_pack_scanned_pdfs"

"""Bernina re-upload incident: a run that claims SO numbers but holds no SO lines.

Damaged shape seen in production (screenshot after the user re-uploaded his
deleted Bernina files): the Filled Order side is intact (full qty + ExMill
value) while the SO side is completely empty — SO qty 0, SO Net 0, every FO
bucket MISSING_ON_SO — and `fo_so_match_so_index` still claims the SO numbers,
so the run keeps reporting Sales Orders it can no longer show.

These tests cover:
  * re-uploading the SO pack from that exact state rebuilds the match;
  * a pack with no usable `line_detail` can never create/overwrite a run with an
    empty SO side (that is what produced the damage);
  * the damage type is detected and healed on the plain Order Desk read path,
    from the archive when a snapshot exists, else by clearing the stale claims;
  * healthy (including legacy, detail-less but valued) runs are left alone;
  * a re-upload of an SO already present stays idempotent;
  * cross-user isolation: a non-owner never heals another user's rows.
"""

from __future__ import annotations

import importlib
import json
import sqlite3

import pytest

import filled_orders_db as fodb

SANTINO_SO = "102876303"
GYM_SO = "102876586"

SANTINO_PRODUCT = "SANTINO PRE 75CMX1.5M ASST12 AW26"
GYM_PRODUCT = "GYM TOWEL DYED 50CMX100CM ASST04 AW26"

SANTINO_VALUE = 5000.0
GYM_VALUE = 2000.0
FO_VALUE = SANTINO_VALUE + GYM_VALUE


def _pack(so_number: str, product: str, qty: float, net: float) -> dict:
    return {
        "meta": {"source_filename": f"{so_number}.pdf"},
        "line_detail": [
            {
                "so_number": so_number,
                "product_name": product,
                "product_detail": product,
                "material_code": product.split()[0],
                "qty": qty,
                "net_amount": net,
                "gst_amount": 0,
                "total_amount": net,
            }
        ],
        "consolidated": [],
        "so_summary": [],
    }


def _seed_fo(db_path: str, user_id: int, distributor_id: int) -> int:
    conn = sqlite3.connect(db_path)
    fodb.ensure_schema(conn)
    fo_id = fodb.create_filled_order(
        conn,
        user_id,
        distributor_id,
        "Bernina Textiles",
        "Bath",
        "AW26",
        source_filename="bernina_towel.xlsx",
    )
    for item_key, brand, size, qty, ex_mill in (
        ("SANTINO|BATH TOWEL", "Santino", "Bath Towel", 100, 50),
        ("GYM TOWEL|GYM TOWEL", "Gym Towel", "Gym Towel", 50, 40),
    ):
        fodb.insert_filled_order_item(
            conn,
            fo_id,
            {
                "item_key": item_key,
                "brand": brand,
                "size": size,
                "product_type": size,
                "raw_qty_value": qty,
                "detected_unit": "pieces",
                "final_piece_qty": qty,
                "is_clean_bale_multiple": True,
                "matched": True,
                "ex_mill_price": ex_mill,
            },
        )
    conn.close()
    return fo_id


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "empty_so_detail.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "empty-so-detail-test-key")

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)

    app = web_app_module.create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(str(db_path))
    users = {}
    for name in ("bd_bernina", "bd_other"):
        user = db.create_user(
            name, "pass123", role="sales_executive", workspace_id="ws-1"
        )
        users[name] = int(user["id"])
    distributor_id = db.add_master_distributor(
        "Bernina Textiles", firm_nick_name="BND", workspace_id="ws-1"
    )

    fo_ids = {
        name: _seed_fo(str(db_path), uid, distributor_id)
        for name, uid in users.items()
    }

    def headers_for(name: str) -> dict:
        login = client.post(
            "/api/v1/auth/login", json={"username": name, "password": "pass123"}
        )
        assert login.status_code == 200, login.get_data(as_text=True)
        return {"Authorization": f"Bearer {login.get_json()['data']['access_token']}"}

    return {
        "client": client,
        "headers": headers_for,
        "users": users,
        "fo_ids": fo_ids,
        "db_path": str(db_path),
    }


def _match(client, headers, fo_id, pack, **extra):
    body = {"filled_order_id": fo_id, "so_pack": pack, "so_buyer_label": "Bernina"}
    body.update(extra)
    return client.post(
        "/api/v1/order-fulfillment/so-pack/match-filled-order",
        json=body,
        headers=headers,
    )


def _run_detail(client, headers, run_id):
    resp = client.get(
        f"/api/v1/order-fulfillment/order-match/{run_id}", headers=headers
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["data"]["run"]


def _so_numbers(run: dict) -> set[str]:
    return {
        str(line.get("so_number")).strip()
        for line in run.get("so_line_detail") or []
        if line.get("so_number")
    }


def _damage_run_to_empty_so(db_path: str, run_id: int, *, claim_so: str) -> None:
    """Reproduce the production shape: SO claims kept, SO line detail gone.

    The FO side of the stored match rows survives (that is what the screenshot
    shows), the SO side of every row is blanked and totals collapse to zero.
    """
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT rows_json, fo_qty, fo_exmill_value FROM fo_so_match_runs WHERE id = ?",
        (int(run_id),),
    ).fetchone()
    rows = json.loads(row[0] or "[]")
    stripped = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        r = dict(r)
        r["so_qty"] = 0
        r["so_net_amount"] = 0
        r["so_numbers"] = []
        r["so_breakdown"] = []
        r["status"] = "MISSING_ON_SO"
        stripped.append(r)
    conn.execute(
        "UPDATE fo_so_match_runs SET so_line_detail_json = NULL, so_qty = 0, "
        "so_net_amount = 0, delta_qty = ?, delta_value = ?, match_count = 0, "
        "fuzzy_count = 0, mismatch_count = 0, missing_count = ?, rows_json = ? "
        "WHERE id = ?",
        (
            -float(row[1] or 0),
            -float(row[2] or 0),
            len(stripped),
            json.dumps(stripped),
            int(run_id),
        ),
    )
    # The stale claim that makes the run keep reporting a Sales Order.
    conn.execute("DELETE FROM fo_so_match_so_index WHERE run_id = ?", (int(run_id),))
    conn.execute(
        "INSERT INTO fo_so_match_so_index (so_number, run_id, user_id, filled_order_id, created_at) "
        "SELECT ?, id, user_id, filled_order_id, '2026-01-01' FROM fo_so_match_runs WHERE id = ?",
        (claim_so, int(run_id)),
    )
    conn.commit()
    conn.close()


def _archive_rows(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM order_desk_archive WHERE restored_at IS NULL"
            ).fetchone()[0]
        )
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


# --------------------------------------------------------------- re-upload


def test_reupload_from_empty_so_run_rebuilds_the_match(env):
    """THE live bug: re-uploading the SO pack must fill the empty match run."""
    client, headers = env["client"], env["headers"]("bd_bernina")
    fo_id = env["fo_ids"]["bd_bernina"]

    first = _match(
        client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE)
    )
    assert first.status_code == 200, first.get_data(as_text=True)
    run_id = first.get_json()["data"]["run_id"]

    _damage_run_to_empty_so(env["db_path"], run_id, claim_so=SANTINO_SO)
    damaged = _run_detail(client, headers, run_id)
    assert float(damaged["so_qty"] or 0) == 0 or _so_numbers(damaged)

    again = _match(
        client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE)
    )
    assert again.status_code == 200, again.get_data(as_text=True)

    fixed = _run_detail(client, headers, again.get_json()["data"]["run_id"])
    assert _so_numbers(fixed) == {SANTINO_SO}
    assert float(fixed["so_qty"]) == pytest.approx(100)
    assert float(fixed["so_net_amount"]) == pytest.approx(SANTINO_VALUE)
    assert int(fixed["missing_count"]) == 1  # the Gym FO bucket has no SO yet
    assert not [
        r
        for r in fixed["rows"]
        if str(r.get("status")) == "MISSING_ON_SO" and r.get("brand") == "Santino"
    ]


def test_pack_without_usable_line_detail_is_refused(env):
    """A consolidated-only pack must never create an empty-SO run."""
    client, headers = env["client"], env["headers"]("bd_bernina")
    fo_id = env["fo_ids"]["bd_bernina"]

    pack = {
        "meta": {"source_filename": "no_lines.zip"},
        "line_detail": [],
        "consolidated": [
            {"so_number": SANTINO_SO, "product_name": SANTINO_PRODUCT, "qty": 100}
        ],
        "so_summary": [{"so_number": SANTINO_SO, "buyer_name": "Bernina"}],
    }
    resp = _match(client, headers, fo_id, pack)
    assert resp.status_code == 400, resp.get_data(as_text=True)

    runs = client.get(
        "/api/v1/order-fulfillment/order-match/list", headers=headers
    ).get_json()["data"]["runs"]
    assert runs == []
    conn = sqlite3.connect(env["db_path"])
    claims = conn.execute("SELECT COUNT(*) FROM fo_so_match_so_index").fetchone()[0]
    conn.close()
    assert int(claims) == 0, "a rejected pack must not claim SO numbers"


def test_reupload_of_present_so_is_idempotent(env):
    client, headers = env["client"], env["headers"]("bd_bernina")
    fo_id = env["fo_ids"]["bd_bernina"]

    first = _match(
        client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE)
    )
    assert first.status_code == 200, first.get_data(as_text=True)
    run_id = first.get_json()["data"]["run_id"]
    before = _run_detail(client, headers, run_id)

    same = _match(
        client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE)
    )
    assert same.status_code == 409, same.get_data(as_text=True)
    assert same.get_json()["error"]["code"] == "so_already_in_system"

    after = _run_detail(client, headers, run_id)
    assert _so_numbers(after) == _so_numbers(before) == {SANTINO_SO}
    assert float(after["so_qty"]) == pytest.approx(float(before["so_qty"]))
    assert float(after["so_net_amount"]) == pytest.approx(
        float(before["so_net_amount"])
    )


# --------------------------------------------------------------- auto-heal


def test_empty_so_detail_is_healed_from_archive_on_plain_read(env):
    """Delete the SO (archived), damage the run, then a normal list read heals it."""
    client, headers = env["client"], env["headers"]("bd_bernina")
    fo_id = env["fo_ids"]["bd_bernina"]

    first = _match(
        client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE)
    )
    assert first.status_code == 200, first.get_data(as_text=True)
    run_id = first.get_json()["data"]["run_id"]
    second = _match(client, headers, fo_id, _pack(GYM_SO, GYM_PRODUCT, 50, GYM_VALUE))
    assert second.status_code == 200, second.get_data(as_text=True)
    run_id = second.get_json()["data"]["run_id"]

    # Wholesale delete → archive snapshot with restore_scope=run, then the run is
    # recreated empty (the damaged shape) while still claiming an SO.
    wiped = client.delete(
        f"/api/v1/order-fulfillment/order-match/{run_id}?confirm_all=1",
        headers=headers,
    )
    assert wiped.status_code == 200, wiped.get_data(as_text=True)
    assert _archive_rows(env["db_path"]) > 0

    conn = sqlite3.connect(env["db_path"])
    conn.execute(
        """
        INSERT INTO fo_so_match_runs (
            user_id, filled_order_id, distributor_id, distributor_name,
            category, season, fo_qty, so_qty, delta_qty, fo_exmill_value,
            so_net_amount, delta_value, match_count, fuzzy_count,
            mismatch_count, missing_count, extra_count, rows_json,
            so_line_detail_json, created_at
        ) VALUES (?, ?, 1, 'Bernina Textiles', 'Bath', 'AW26', 150, 0, -150,
                  ?, 0, ?, 0, 0, 0, 2, 0, '[]', NULL, '2026-02-02')
        """,
        (env["users"]["bd_bernina"], fo_id, FO_VALUE, -FO_VALUE),
    )
    empty_run_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.execute(
        "INSERT INTO fo_so_match_so_index (so_number, run_id, user_id, filled_order_id, created_at) "
        "VALUES (?, ?, ?, ?, '2026-02-02')",
        (SANTINO_SO, empty_run_id, env["users"]["bd_bernina"], fo_id),
    )
    conn.commit()
    conn.close()

    listed = client.get(
        "/api/v1/order-fulfillment/order-match/list", headers=headers
    )
    assert listed.status_code == 200, listed.get_data(as_text=True)
    runs = [r for r in listed.get_json()["data"]["runs"] if r["filled_order_id"] == fo_id]
    assert len(runs) == 1
    healed = _run_detail(client, headers, int(runs[0]["id"]))
    assert _so_numbers(healed) == {SANTINO_SO, GYM_SO}
    assert float(healed["so_net_amount"]) == pytest.approx(FO_VALUE)
    assert int(healed["missing_count"]) == 0


def test_empty_so_detail_without_archive_clears_stale_claims(env):
    """No snapshot to restore → free the SO claims, keep the FO side, stay silent."""
    client, headers = env["client"], env["headers"]("bd_bernina")
    fo_id = env["fo_ids"]["bd_bernina"]

    first = _match(
        client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE)
    )
    assert first.status_code == 200, first.get_data(as_text=True)
    run_id = first.get_json()["data"]["run_id"]
    _damage_run_to_empty_so(env["db_path"], run_id, claim_so=SANTINO_SO)

    conn = sqlite3.connect(env["db_path"])
    conn.execute("DELETE FROM order_desk_archive")
    conn.commit()
    conn.close()

    listed = client.get(
        "/api/v1/order-fulfillment/order-match/list", headers=headers
    )
    assert listed.status_code == 200, listed.get_data(as_text=True)

    conn = sqlite3.connect(env["db_path"])
    claims = conn.execute(
        "SELECT COUNT(*) FROM fo_so_match_so_index WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    fo_qty = conn.execute(
        "SELECT fo_qty FROM fo_so_match_runs WHERE id = ?", (run_id,)
    ).fetchone()
    fo_items = conn.execute(
        "SELECT COUNT(*) FROM filled_order_items WHERE filled_order_id = ?", (fo_id,)
    ).fetchone()[0]
    conn.close()
    assert int(claims) == 0, "stale SO claims must be freed"
    assert fo_qty is not None and float(fo_qty[0] or 0) > 0, "FO side must survive"
    assert int(fo_items) == 2, "FO lines must never be deleted"

    # A re-upload is now accepted cleanly and rebuilds the match.
    again = _match(
        client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE)
    )
    assert again.status_code == 200, again.get_data(as_text=True)
    fixed = _run_detail(client, headers, again.get_json()["data"]["run_id"])
    assert _so_numbers(fixed) == {SANTINO_SO}
    assert float(fixed["so_net_amount"]) == pytest.approx(SANTINO_VALUE)


def test_healthy_and_legacy_runs_are_left_untouched(env):
    """No writes for healthy runs, including legacy runs without line detail."""
    client, headers = env["client"], env["headers"]("bd_bernina")
    fo_id = env["fo_ids"]["bd_bernina"]

    first = _match(
        client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE)
    )
    assert first.status_code == 200, first.get_data(as_text=True)
    run_id = first.get_json()["data"]["run_id"]

    # Legacy shape: no so_line_detail_json, but real SO qty/value + claims.
    conn = sqlite3.connect(env["db_path"])
    conn.execute(
        "UPDATE fo_so_match_runs SET so_line_detail_json = NULL WHERE id = ?",
        (run_id,),
    )
    conn.commit()
    before = conn.execute(
        "SELECT so_qty, so_net_amount, missing_count, rows_json FROM fo_so_match_runs "
        "WHERE id = ?",
        (run_id,),
    ).fetchone()
    claims_before = conn.execute(
        "SELECT so_number FROM fo_so_match_so_index WHERE run_id = ?", (run_id,)
    ).fetchall()
    conn.close()

    from app.services import fo_so_match_repair as repairsvc

    conn = sqlite3.connect(env["db_path"])
    probe = repairsvc.damage_probe(
        conn,
        scope=repairsvc.RepairScope.for_user(env["users"]["bd_bernina"]),
    )
    conn.close()
    assert probe["damaged"] is False, probe

    assert (
        client.get(
            "/api/v1/order-fulfillment/order-match/list", headers=headers
        ).status_code
        == 200
    )
    conn = sqlite3.connect(env["db_path"])
    after = conn.execute(
        "SELECT so_qty, so_net_amount, missing_count, rows_json FROM fo_so_match_runs "
        "WHERE id = ?",
        (run_id,),
    ).fetchone()
    claims_after = conn.execute(
        "SELECT so_number FROM fo_so_match_so_index WHERE run_id = ?", (run_id,)
    ).fetchall()
    conn.close()
    assert after == before
    assert claims_after == claims_before


def test_cross_user_isolation_for_empty_so_heal(env):
    """A non-owner's read must not touch another user's damaged rows."""
    client = env["client"]
    owner_headers = env["headers"]("bd_bernina")
    other_headers = env["headers"]("bd_other")
    fo_id = env["fo_ids"]["bd_bernina"]

    first = _match(
        client,
        owner_headers,
        fo_id,
        _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE),
    )
    assert first.status_code == 200, first.get_data(as_text=True)
    run_id = first.get_json()["data"]["run_id"]
    _damage_run_to_empty_so(env["db_path"], run_id, claim_so=SANTINO_SO)

    conn = sqlite3.connect(env["db_path"])
    before = conn.execute(
        "SELECT so_qty, so_net_amount, rows_json, so_line_detail_json "
        "FROM fo_so_match_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    conn.close()

    other = client.get(
        "/api/v1/order-fulfillment/order-match/list", headers=other_headers
    )
    assert other.status_code == 200, other.get_data(as_text=True)
    assert other.get_json()["data"]["runs"] == []

    conn = sqlite3.connect(env["db_path"])
    after = conn.execute(
        "SELECT so_qty, so_net_amount, rows_json, so_line_detail_json "
        "FROM fo_so_match_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    claims = conn.execute(
        "SELECT COUNT(*) FROM fo_so_match_so_index WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    conn.close()
    assert after == before, "another user's read must not heal these rows"
    assert int(claims) == 1, "another user must not clear these claims"

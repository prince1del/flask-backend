"""Order Desk deletes are recoverable: re-upload the same data, get it all back.

"Fool-proof" requirement: even a *deliberate* delete (one SO, a whole FO↔SO
match with confirm_all, or an SO/CI tracking row with its reconciliation items /
achievements / payment entries) must be undone by uploading the same source data
again — not just the part the uploaded file happens to contain.

Pinned here:
  * delete ONE SO → re-upload that SO file → lines, statuses (no spurious
    MISSING_ON_SO) and totals return, other SOs untouched;
  * delete the WHOLE match (confirm_all) → re-upload one SO pack → the whole
    match returns, including the SOs that were not in the uploaded file;
  * repeating an upload never doubles qty / value;
  * an archived SO of user A can never be restored into user B's data;
  * a deleted tracking row's items / achievements / payment entries come back on
    re-upload, and a per-SO ("entity" scope) delete is never resurrected behind
    the user's back by an unrelated upload.
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest

import filled_orders_db as fodb

SANTINO_SO = "102876303"
GYM_SO = "102876586"
SANTINO_PRODUCT = "SANTINO PRE 75CMX1.5M ASST12 AW26"
GYM_PRODUCT = "GYM TOWEL DYED 50CMX100CM ASST04 AW26"
SANTINO_VALUE = 5000.0  # 100 pcs × 50 ex-mill
GYM_VALUE = 2000.0  # 50 pcs × 40 ex-mill
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


def _create_fo(db_path: str, user_id: int, distributor_id: int) -> int:
    conn = sqlite3.connect(db_path)
    try:
        fodb.ensure_schema(conn)
        fo_id = fodb.create_filled_order(
            conn, user_id, distributor_id, "Bernina Textiles", "Bath", "AW26",
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
        return fo_id
    finally:
        conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "order_desk_recycle.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "order-desk-recycle-key")

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)

    app = web_app_module.create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(str(db_path))
    users: dict[str, int] = {}
    for uname in ("bd_a", "bd_b"):
        row = db.create_user(
            uname, "pass123", role="sales_executive", workspace_id="ws-1"
        )
        users[uname] = int(row["id"])

    dists = {
        uname: db.add_master_distributor(
            f"Bernina Textiles {uname}",
            firm_nick_name=f"BND-{uname}",
            workspace_id="ws-1",
            user_id=uid,
        )
        for uname, uid in users.items()
    }
    fos = {
        uname: _create_fo(str(db_path), users[uname], dists[uname])
        for uname in users
    }

    def login(username: str) -> dict:
        resp = client.post(
            "/api/v1/auth/login", json={"username": username, "password": "pass123"}
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        return {"Authorization": f"Bearer {resp.get_json()['data']['access_token']}"}

    return {
        "client": client,
        "db": db,
        "db_path": str(db_path),
        "users": users,
        "dists": dists,
        "fos": fos,
        "login": login,
    }


def _match(client, headers, fo_id, pack):
    return client.post(
        "/api/v1/order-fulfillment/so-pack/match-filled-order",
        json={"filled_order_id": fo_id, "so_pack": pack, "so_buyer_label": "Bernina"},
        headers=headers,
    )


def _latest_run(db_path: str, fo_id: int, user_id: int) -> dict | None:
    from app.services import fo_so_revision as sorev

    conn = sqlite3.connect(db_path)
    try:
        return sorev.get_latest_run_for_fo(
            conn, user_id=int(user_id), filled_order_id=int(fo_id)
        )
    finally:
        conn.close()


def _so_numbers(run: dict | None) -> set[str]:
    return {
        str(line.get("so_number")).strip()
        for line in (run or {}).get("so_line_detail") or []
        if line.get("so_number")
    }


def _archive_rows(db_path: str, kind: str | None = None) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        sql = (
            "SELECT id, user_id, kind, entity_key, restore_scope, restored_at "
            "FROM order_desk_archive"
        )
        params: tuple = ()
        if kind:
            sql += " WHERE kind = ?"
            params = (kind,)
        return conn.execute(sql + " ORDER BY id", params).fetchall()
    finally:
        conn.close()


def _seed_both_sos(env) -> tuple[dict, int, int]:
    client = env["client"]
    headers = env["login"]("bd_a")
    fo_id = env["fos"]["bd_a"]
    first = _match(client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE))
    assert first.status_code == 200, first.get_data(as_text=True)
    second = _match(client, headers, fo_id, _pack(GYM_SO, GYM_PRODUCT, 50, GYM_VALUE))
    assert second.status_code == 200, second.get_data(as_text=True)
    run_id = int(second.get_json()["data"]["run_id"])
    run = _latest_run(env["db_path"], fo_id, env["users"]["bd_a"])
    assert _so_numbers(run) == {SANTINO_SO, GYM_SO}
    assert float(run["so_net_amount"]) == pytest.approx(FO_VALUE)
    return headers, fo_id, run_id


def test_delete_one_so_then_reupload_that_file_restores_everything(env):
    client = env["client"]
    headers, fo_id, run_id = _seed_both_sos(env)

    deleted = client.delete(
        f"/api/v1/order-fulfillment/order-match/{run_id}?so_number={SANTINO_SO}",
        headers=headers,
    )
    assert deleted.status_code == 200, deleted.get_data(as_text=True)

    # The deleted SO's lines were recycled, not destroyed.
    archived = [r for r in _archive_rows(env["db_path"], "match_so")]
    assert [r[3] for r in archived] == [SANTINO_SO]
    assert archived[0][4] == "entity", "a single-SO delete must stay entity-scoped"

    mid = _latest_run(env["db_path"], fo_id, env["users"]["bd_a"])
    assert _so_numbers(mid) == {GYM_SO}
    assert float(mid["so_net_amount"]) == pytest.approx(GYM_VALUE)

    again = _match(client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE))
    assert again.status_code == 200, again.get_data(as_text=True)

    restored = _latest_run(env["db_path"], fo_id, env["users"]["bd_a"])
    assert _so_numbers(restored) == {SANTINO_SO, GYM_SO}
    assert int(restored["missing_count"]) == 0, restored["rows"]
    assert not [r for r in restored["rows"] if str(r.get("status")) == "MISSING_ON_SO"]
    assert float(restored["so_net_amount"]) == pytest.approx(FO_VALUE)
    assert float(restored["delta_value"]) == pytest.approx(0.0)


def test_delete_whole_match_with_confirm_all_then_reupload_restores_full_match(env):
    client = env["client"]
    headers, fo_id, run_id = _seed_both_sos(env)

    # The 409 multi-SO guard still stands.
    blind = client.delete(
        f"/api/v1/order-fulfillment/order-match/{run_id}", headers=headers
    )
    assert blind.status_code == 409, blind.get_data(as_text=True)
    assert blind.get_json()["error"]["code"] == "match_run_has_multiple_so"

    wiped = client.delete(
        f"/api/v1/order-fulfillment/order-match/{run_id}?confirm_all=1", headers=headers
    )
    assert wiped.status_code == 200, wiped.get_data(as_text=True)
    assert _latest_run(env["db_path"], fo_id, env["users"]["bd_a"]) is None

    # Both SOs were recycled with run scope (a wholesale destruction).
    archived = _archive_rows(env["db_path"], "match_so")
    assert {r[3] for r in archived} == {SANTINO_SO, GYM_SO}
    assert {r[4] for r in archived} == {"run"}

    # Re-upload only ONE of the two SO files — the other must come back too.
    again = _match(client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE))
    assert again.status_code == 200, again.get_data(as_text=True)

    restored = _latest_run(env["db_path"], fo_id, env["users"]["bd_a"])
    assert _so_numbers(restored) == {SANTINO_SO, GYM_SO}
    assert int(restored["missing_count"]) == 0, restored["rows"]
    assert not [r for r in restored["rows"] if str(r.get("status")) == "MISSING_ON_SO"]
    assert float(restored["so_net_amount"]) == pytest.approx(FO_VALUE)
    assert float(restored["fo_exmill_value"]) == pytest.approx(FO_VALUE)
    assert float(restored["delta_value"]) == pytest.approx(0.0)


def test_restore_is_idempotent_when_the_same_file_is_uploaded_twice(env):
    client = env["client"]
    headers, fo_id, run_id = _seed_both_sos(env)
    client.delete(
        f"/api/v1/order-fulfillment/order-match/{run_id}?confirm_all=1", headers=headers
    )
    first = _match(client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE))
    assert first.status_code == 200, first.get_data(as_text=True)
    after_first = _latest_run(env["db_path"], fo_id, env["users"]["bd_a"])

    second = _match(client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE))
    # Either the unchanged-SO guard fires, or the upload is absorbed — but the
    # numbers must not move either way.
    if second.status_code == 409:
        assert second.get_json()["error"]["code"] in (
            "so_already_in_system",
            "duplicate_sales_order",
        )
    else:
        assert second.status_code == 200, second.get_data(as_text=True)

    after_second = _latest_run(env["db_path"], fo_id, env["users"]["bd_a"])
    assert _so_numbers(after_second) == _so_numbers(after_first) == {SANTINO_SO, GYM_SO}
    assert float(after_second["so_qty"]) == pytest.approx(float(after_first["so_qty"]))
    assert float(after_second["so_net_amount"]) == pytest.approx(FO_VALUE)
    assert len(after_second["so_line_detail"]) == len(after_first["so_line_detail"])


def test_archived_so_of_user_a_is_never_restored_into_user_b(env):
    client = env["client"]
    headers_a, fo_a, run_a = _seed_both_sos(env)
    client.delete(
        f"/api/v1/order-fulfillment/order-match/{run_a}?confirm_all=1", headers=headers_a
    )
    assert {r[3] for r in _archive_rows(env["db_path"], "match_so")} == {
        SANTINO_SO,
        GYM_SO,
    }

    # User B uploads their own SO against their own FO. A's archive is invisible.
    headers_b = env["login"]("bd_b")
    fo_b = env["fos"]["bd_b"]
    b_so = "902876303"
    resp = _match(client, headers_b, fo_b, _pack(b_so, SANTINO_PRODUCT, 100, SANTINO_VALUE))
    assert resp.status_code == 200, resp.get_data(as_text=True)

    run_b = _latest_run(env["db_path"], fo_b, env["users"]["bd_b"])
    assert _so_numbers(run_b) == {b_so}
    assert float(run_b["so_net_amount"]) == pytest.approx(SANTINO_VALUE)
    # A's rows are still archived (unrestored), still owned by A.
    rows = _archive_rows(env["db_path"], "match_so")
    assert {r[1] for r in rows} == {env["users"]["bd_a"]}
    assert all(r[5] is None for r in rows)


def test_entity_scoped_delete_is_not_resurrected_by_an_unrelated_upload(env):
    """A deliberately deleted single SO stays gone until that SO is re-uploaded."""
    client = env["client"]
    headers, fo_id, run_id = _seed_both_sos(env)
    client.delete(
        f"/api/v1/order-fulfillment/order-match/{run_id}?so_number={SANTINO_SO}",
        headers=headers,
    )
    other_so = "302876999"
    resp = _match(client, headers, fo_id, _pack(other_so, GYM_PRODUCT, 10, 400.0))
    assert resp.status_code == 200, resp.get_data(as_text=True)

    run = _latest_run(env["db_path"], fo_id, env["users"]["bd_a"])
    assert SANTINO_SO not in _so_numbers(run)
    assert _so_numbers(run) == {GYM_SO, other_so}


# ------------------------------------------------------- SO/CI tracking rows


def _seed_tracking(env, username: str) -> tuple[int, str]:
    """A tracking row with reconciliation items, an achievement and a payment."""
    db = env["db"]
    order_ref = f"SO-{username}-777"
    tracking_id = db.create_order_lifecycle_tracking(
        order_ref_no=order_ref,
        distributor_id=env["dists"][username],
        sales_order_file_reference=f"/tmp/{order_ref}.pdf",
        workspace_id="ws-1",
    )
    db.upsert_order_lifecycle_item(
        tracking_id=tracking_id,
        item_name=SANTINO_PRODUCT,
        source="so",
        qty=100,
        value=SANTINO_VALUE,
        workspace_id="ws-1",
        item_key="SANTINO|BATH TOWEL",
    )
    db.create_achievement(tracking_id, SANTINO_VALUE, workspace_id="ws-1")
    conn = sqlite3.connect(env["db_path"])
    try:
        conn.execute(
            "INSERT INTO distributor_payment_entries "
            "(workspace_id, distributor_id, tracking_id, order_ref_no, amount, "
            " payment_date, created_at) VALUES ('ws-1', ?, ?, ?, ?, ?, ?)",
            (
                env["dists"][username],
                tracking_id,
                order_ref,
                SANTINO_VALUE,
                "2026-01-01",
                "2026-01-01",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return int(tracking_id), order_ref


def _tracking_child_counts(db_path: str, tracking_id: int) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            "items": conn.execute(
                "SELECT COUNT(*) FROM order_fulfillment_items WHERE order_lifecycle_id = ?",
                (int(tracking_id),),
            ).fetchone()[0],
            "achievements": conn.execute(
                "SELECT COUNT(*) FROM achievements WHERE order_lifecycle_tracking_id = ?",
                (int(tracking_id),),
            ).fetchone()[0],
            "payments": conn.execute(
                "SELECT COUNT(*) FROM distributor_payment_entries WHERE tracking_id = ?",
                (int(tracking_id),),
            ).fetchone()[0],
        }
    finally:
        conn.close()


def test_deleted_tracking_row_is_restored_when_the_same_so_is_uploaded_again(env):
    client = env["client"]
    headers = env["login"]("bd_a")
    tracking_id, order_ref = _seed_tracking(env, "bd_a")
    before = _tracking_child_counts(env["db_path"], tracking_id)
    assert before == {"items": 1, "achievements": 1, "payments": 1}

    deleted = client.delete(
        f"/api/v1/order-fulfillment/tracking/{tracking_id}", headers=headers
    )
    assert deleted.status_code == 200, deleted.get_data(as_text=True)
    assert _tracking_child_counts(env["db_path"], tracking_id) == {
        "items": 0,
        "achievements": 0,
        "payments": 0,
    }
    archived = _archive_rows(env["db_path"], "tracking")
    assert [r[3] for r in archived] == [order_ref.upper()]
    assert archived[0][1] == env["users"]["bd_a"]

    # Re-uploading the same SO creates a fresh tracking row; the restore hook
    # (the same one the SO PDF upload route calls) re-attaches its data.
    from app.services import order_desk_archive as archive

    new_tracking_id = env["db"].create_order_lifecycle_tracking(
        order_ref_no=order_ref,
        distributor_id=env["dists"]["bd_a"],
        sales_order_file_reference=f"/tmp/{order_ref}.pdf",
        workspace_id="ws-1",
    )
    restored = archive.restore_tracking_for_upload(
        env["db_path"],
        user_id=env["users"]["bd_a"],
        workspace_id="ws-1",
        order_ref_no=order_ref,
        tracking_id=new_tracking_id,
    )
    assert restored is not None
    assert _tracking_child_counts(env["db_path"], new_tracking_id) == before

    # Idempotent: a second identical upload must not double anything.
    again = archive.restore_tracking_for_upload(
        env["db_path"],
        user_id=env["users"]["bd_a"],
        workspace_id="ws-1",
        order_ref_no=order_ref,
        tracking_id=new_tracking_id,
    )
    assert again is None
    assert _tracking_child_counts(env["db_path"], new_tracking_id) == before


def test_tracking_archive_of_user_a_cannot_be_restored_by_user_b(env):
    client = env["client"]
    headers_a = env["login"]("bd_a")
    tracking_id, order_ref = _seed_tracking(env, "bd_a")
    assert (
        client.delete(
            f"/api/v1/order-fulfillment/tracking/{tracking_id}", headers=headers_a
        ).status_code
        == 200
    )

    from app.services import order_desk_archive as archive

    b_tracking_id = env["db"].create_order_lifecycle_tracking(
        order_ref_no=order_ref,
        distributor_id=env["dists"]["bd_b"],
        workspace_id="ws-1",
    )
    stolen = archive.restore_tracking_for_upload(
        env["db_path"],
        user_id=env["users"]["bd_b"],
        workspace_id="ws-1",
        order_ref_no=order_ref,
        tracking_id=b_tracking_id,
    )
    assert stolen is None
    assert _tracking_child_counts(env["db_path"], b_tracking_id) == {
        "items": 0,
        "achievements": 0,
        "payments": 0,
    }


def test_retention_purge_drops_expired_archive_rows_only(env):
    from app.services import order_desk_archive as archive

    client = env["client"]
    headers, fo_id, run_id = _seed_both_sos(env)
    client.delete(
        f"/api/v1/order-fulfillment/order-match/{run_id}?confirm_all=1", headers=headers
    )
    assert _archive_rows(env["db_path"], "match_so")

    conn = sqlite3.connect(env["db_path"])
    try:
        # Fresh rows survive.
        assert archive.purge_expired(conn)["rows_purged"] == 0
        conn.execute("UPDATE order_desk_archive SET deleted_at = '2000-01-01 00:00:00'")
        conn.commit()
        purged = archive.purge_expired(conn)
        assert purged["rows_purged"] > 0
    finally:
        conn.close()
    assert _archive_rows(env["db_path"]) == []

"""Order Match self-heal: damaged FO ↔ SO data repairs itself, silently.

The user must not have to run a repair tool. Reading / uploading through the
normal Order Desk routes detects the damaged shape (several match runs for one
Filled Order, or orphan `fo_so_match_so_index` rows) and consolidates it.

Also pins the scope rules: a normal user heals only their own rows; only the
workspace owner (`WORKSPACE_OWNER_USERNAME`, `is_workspace_owner=1`) gets the
workspace-wide scope, and it is derived from the signed JWT, not from input.
"""

from __future__ import annotations

import importlib
import json
import sqlite3

import pytest

import filled_orders_db as fodb

SANTINO_SO = "102876303"
GYM_SO = "102876586"
B_SANTINO_SO = "202876303"
B_GYM_SO = "202876586"
SANTINO_PRODUCT = "SANTINO PRE 75CMX1.5M ASST12 AW26"
GYM_PRODUCT = "GYM TOWEL DYED 50CMX100CM ASST04 AW26"
SANTINO_VALUE = 5000.0  # 100 pcs × 50 ex-mill
GYM_VALUE = 2000.0  # 50 pcs × 40 ex-mill
FO_VALUE = SANTINO_VALUE + GYM_VALUE

OWNER_USERNAME = "kunwar1del"


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


def _create_fo(db_path: str, user_id: int, distributor_id: int, name: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        fodb.ensure_schema(conn)
        fo_id = fodb.create_filled_order(
            conn, user_id, distributor_id, name, "Bath", "AW26",
            source_filename="towel.xlsx",
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
    db_path = tmp_path / "order_match_autoheal.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "order-match-autoheal-key")
    monkeypatch.setenv("WORKSPACE_OWNER_USERNAME", OWNER_USERNAME)

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
    for uname in ("bd_a", "bd_b", OWNER_USERNAME):
        row = db.create_user(
            uname, "pass123", role="sales_executive", workspace_id="ws-1"
        )
        users[uname] = int(row["id"])

    dist_id = db.add_master_distributor(
        "Bernina Textiles", firm_nick_name="BND", workspace_id="ws-1"
    )
    fos = {
        uname: _create_fo(str(db_path), uid, dist_id, "Bernina Textiles")
        for uname, uid in users.items()
    }

    def login(username: str) -> dict:
        resp = client.post(
            "/api/v1/auth/login", json={"username": username, "password": "pass123"}
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        return {"Authorization": f"Bearer {resp.get_json()['data']['access_token']}"}

    return {
        "app": app,
        "client": client,
        "db": db,
        "db_path": str(db_path),
        "users": users,
        "fos": fos,
        "login": login,
    }


def _match(client, headers, fo_id, pack):
    return client.post(
        "/api/v1/order-fulfillment/so-pack/match-filled-order",
        json={"filled_order_id": fo_id, "so_pack": pack, "so_buyer_label": "Bernina"},
        headers=headers,
    )


def _runs_for(db_path: str, fo_id: int, user_id: int) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT id, so_qty, so_net_amount, missing_count FROM fo_so_match_runs "
            "WHERE filled_order_id = ? AND user_id = ? ORDER BY id",
            (int(fo_id), int(user_id)),
        ).fetchall()
    finally:
        conn.close()


def _damage_fo(
    db_path: str, *, user_id: int, fo_id: int, rival_so: str = GYM_SO
) -> None:
    """Recreate the production damage: a rival run that knows only one SO,
    plus an SO index row whose run is gone."""
    from app.services import fo_so_match_db as matchdb

    conn = sqlite3.connect(db_path)
    try:
        matchdb.ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO fo_so_match_runs (
                user_id, filled_order_id, distributor_id, distributor_name,
                category, season, so_qty, so_net_amount, missing_count,
                rows_json, so_line_detail_json, created_at
            ) VALUES (?, ?, 1, 'Bernina Textiles', 'Bath', 'AW26', 50, ?, 1,
                      '[]', ?, '2026-01-01')
            """,
            (
                int(user_id),
                int(fo_id),
                GYM_VALUE,
                json.dumps(
                    [
                        {
                            "so_number": rival_so,
                            "product_name": GYM_PRODUCT,
                            "product_detail": GYM_PRODUCT,
                            "material_code": "GYM",
                            "qty": 50,
                            "net_amount": GYM_VALUE,
                        }
                    ]
                ),
            ),
        )
        conn.execute(
            "INSERT INTO fo_so_match_so_index "
            "(so_number, run_id, user_id, filled_order_id, created_at) "
            "VALUES (?, ?, ?, ?, '2026-01-01')",
            (f"99999{user_id}", 987650 + int(user_id), int(user_id), int(fo_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_healthy_run(client, headers, fo_id, so_number: str = SANTINO_SO) -> int:
    resp = _match(client, headers, fo_id, _pack(so_number, SANTINO_PRODUCT, 100, SANTINO_VALUE))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return int(resp.get_json()["data"]["run_id"])


def _orphan_index_count(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM fo_so_match_so_index i WHERE NOT EXISTS "
            "(SELECT 1 FROM fo_so_match_runs r WHERE r.id = i.run_id)"
        ).fetchone()[0]
    finally:
        conn.close()


def test_damaged_fo_heals_itself_on_normal_order_desk_read(env):
    client, db_path = env["client"], env["db_path"]
    headers = env["login"]("bd_a")
    user_id, fo_id = env["users"]["bd_a"], env["fos"]["bd_a"]
    _seed_healthy_run(client, headers, fo_id)
    _damage_fo(db_path, user_id=user_id, fo_id=fo_id)
    assert len(_runs_for(db_path, fo_id, user_id)) == 2
    assert _orphan_index_count(db_path) == 1

    # No repair call — just the normal Order Desk list the app already makes.
    listed = client.get(
        "/api/v1/order-fulfillment/order-match/list", headers=headers
    )
    assert listed.status_code == 200, listed.get_data(as_text=True)

    rows = _runs_for(db_path, fo_id, user_id)
    assert len(rows) == 1, "duplicate runs must be consolidated"
    assert float(rows[0][2]) == pytest.approx(FO_VALUE)
    assert int(rows[0][3]) == 0, "no spurious MISSING_ON_SO left"
    assert _orphan_index_count(db_path) == 0

    # The response the user sees carries the healed totals, with no warning text.
    runs = listed.get_json()["data"]["runs"]
    mine = [r for r in runs if r["filled_order_id"] == fo_id]
    assert len(mine) == 1
    assert float(mine[0]["so_net_amount"]) == pytest.approx(FO_VALUE)


def test_damaged_fo_heals_itself_on_so_pack_upload(env):
    client, db_path = env["client"], env["db_path"]
    headers = env["login"]("bd_a")
    user_id, fo_id = env["users"]["bd_a"], env["fos"]["bd_a"]
    _seed_healthy_run(client, headers, fo_id)
    _damage_fo(db_path, user_id=user_id, fo_id=fo_id)

    # Re-uploading the SO that only the rival run knew must not fail with a
    # stale-index error: the upload path heals first, so the SO is now correctly
    # recognised as already present on the consolidated run.
    again = _match(client, headers, fo_id, _pack(GYM_SO, GYM_PRODUCT, 50, GYM_VALUE))
    if again.status_code != 200:
        assert again.status_code == 409, again.get_data(as_text=True)
        assert (
            again.get_json()["error"]["code"] == "so_already_in_system"
        ), again.get_data(as_text=True)

    rows = _runs_for(db_path, fo_id, user_id)
    assert len(rows) == 1
    assert float(rows[0][2]) == pytest.approx(FO_VALUE)
    assert int(rows[0][3]) == 0


def test_autoheal_is_a_noop_and_writes_nothing_when_healthy(env):
    client, db_path = env["client"], env["db_path"]
    headers = env["login"]("bd_a")
    user_id, fo_id = env["users"]["bd_a"], env["fos"]["bd_a"]
    _seed_healthy_run(client, headers, fo_id)

    from app.services import fo_so_match_repair as repairsvc

    conn = sqlite3.connect(db_path)
    try:
        scope = repairsvc.RepairScope.for_user(user_id)
        assert repairsvc.damage_probe(conn, scope=scope)["damaged"] is False
        assert repairsvc.autoheal(conn, scope=scope, reason="test") is None
    finally:
        conn.close()

    before = _runs_for(db_path, fo_id, user_id)
    for _ in range(3):
        assert (
            client.get(
                "/api/v1/order-fulfillment/order-match/list", headers=headers
            ).status_code
            == 200
        )
    assert _runs_for(db_path, fo_id, user_id) == before, "healthy data must not be rewritten"


def test_normal_user_autoheal_never_touches_another_users_rows(env):
    client, db_path = env["client"], env["db_path"]
    a_headers, b_headers = env["login"]("bd_a"), env["login"]("bd_b")
    a_uid, b_uid = env["users"]["bd_a"], env["users"]["bd_b"]
    a_fo, b_fo = env["fos"]["bd_a"], env["fos"]["bd_b"]

    # SO numbers are globally unique, so each user gets their own.
    _seed_healthy_run(client, a_headers, a_fo, SANTINO_SO)
    _seed_healthy_run(client, b_headers, b_fo, B_SANTINO_SO)
    _damage_fo(db_path, user_id=a_uid, fo_id=a_fo, rival_so=GYM_SO)
    _damage_fo(db_path, user_id=b_uid, fo_id=b_fo, rival_so=B_GYM_SO)

    b_before = _runs_for(db_path, b_fo, b_uid)
    assert (
        client.get(
            "/api/v1/order-fulfillment/order-match/list", headers=a_headers
        ).status_code
        == 200
    )
    assert len(_runs_for(db_path, a_fo, a_uid)) == 1, "own rows healed"
    assert _runs_for(db_path, b_fo, b_uid) == b_before, "other user's rows untouched"


def test_workspace_owner_scope_is_workspace_wide_and_cannot_be_spoofed(env):
    client, db, db_path = env["client"], env["db"], env["db_path"]
    b_headers = env["login"]("bd_b")
    b_uid, b_fo = env["users"]["bd_b"], env["fos"]["bd_b"]
    _seed_healthy_run(client, b_headers, b_fo)
    _damage_fo(db_path, user_id=b_uid, fo_id=b_fo)

    # Promote the supreme owner (flag only — no data takeover, so bd_b keeps its rows).
    db.promote_workspace_owner(OWNER_USERNAME, takeover_workspace_data=False)
    assert db.is_workspace_owner_user(env["users"][OWNER_USERNAME]) is True

    from app.routes import data as data_routes
    from app.services import fo_so_match_repair as repairsvc

    app = env["app"]

    # A normal user cannot obtain the workspace scope, even by sending flags.
    with app.test_request_context(
        "/api/v1/order-fulfillment/order-match/list",
        json={"global_scope": True, "is_workspace_owner": True},
    ):
        from flask import request as flask_request

        flask_request.user = {"user_id": b_uid, "role": "sales_executive"}
        scope = repairsvc.RepairScope.for_request(b_uid)
        assert scope.global_scope is False
        assert scope.user_filter == b_uid

    # The owner's scope spans the workspace.
    owner_uid = env["users"][OWNER_USERNAME]
    with app.test_request_context("/api/v1/order-fulfillment/order-match/list"):
        from flask import request as flask_request

        flask_request.user = {
            "user_id": owner_uid,
            "role": "sales_executive",
            "is_workspace_owner": True,
        }
        scope = repairsvc.RepairScope.for_request(owner_uid)
        assert scope.global_scope is True
        assert scope.user_filter is None

        conn = sqlite3.connect(db_path)
        try:
            probe = repairsvc.damage_probe(conn, scope=scope)
            assert (b_uid, b_fo) in probe["duplicate_run_groups"]
            data_routes._autoheal_order_match(
                conn, user_id=owner_uid, reason="owner-global"
            )
        finally:
            conn.close()

    rows = _runs_for(db_path, b_fo, b_uid)
    assert len(rows) == 1, "owner heals workspace-wide"
    assert float(rows[0][2]) == pytest.approx(FO_VALUE)
    assert _orphan_index_count(db_path) == 0

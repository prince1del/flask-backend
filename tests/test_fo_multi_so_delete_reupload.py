"""One FO with several SOs: deleting one SO must not break the other SOs.

Reproduces the Bernina AW26 incident — a Filled Order carried several Sales
Orders, the user deleted one of them from Order Desk and re-uploaded it, and
afterwards the whole FO showed MISSING_ON_SO lines with a zero order value.
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


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "fo_multi_so.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "fo-multi-so-test-key")

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)

    app = web_app_module.create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(str(db_path))
    user = db.create_user(
        "bd_bernina", "pass123", role="sales_executive", workspace_id="ws-1"
    )
    user_id = int(user["id"])
    distributor_id = db.add_master_distributor(
        "Bernina Textiles", firm_nick_name="BND", workspace_id="ws-1"
    )

    conn = sqlite3.connect(str(db_path))
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

    login = client.post(
        "/api/v1/auth/login", json={"username": "bd_bernina", "password": "pass123"}
    )
    assert login.status_code == 200, login.get_data(as_text=True)
    token = login.get_json()["data"]["access_token"]
    return client, {"Authorization": f"Bearer {token}"}, fo_id, str(db_path)


def _match(client, headers, fo_id, pack):
    resp = client.post(
        "/api/v1/order-fulfillment/so-pack/match-filled-order",
        json={"filled_order_id": fo_id, "so_pack": pack, "so_buyer_label": "Bernina"},
        headers=headers,
    )
    return resp


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


def test_delete_one_so_keeps_other_so_then_reupload_restores_totals(env):
    client, headers, fo_id, _db = env

    first = _match(client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE))
    assert first.status_code == 200, first.get_data(as_text=True)
    second = _match(client, headers, fo_id, _pack(GYM_SO, GYM_PRODUCT, 50, GYM_VALUE))
    assert second.status_code == 200, second.get_data(as_text=True)
    run_id = second.get_json()["data"]["run_id"]

    both = _run_detail(client, headers, run_id)
    assert _so_numbers(both) == {SANTINO_SO, GYM_SO}
    assert float(both["so_net_amount"]) == pytest.approx(FO_VALUE)
    assert int(both["missing_count"]) == 0

    # A blind whole-run delete would wipe the FO's other SOs → must be refused.
    blind = client.delete(
        f"/api/v1/order-fulfillment/order-match/{run_id}", headers=headers
    )
    assert blind.status_code == 409, blind.get_data(as_text=True)
    assert blind.get_json()["error"]["code"] == "match_run_has_multiple_so"
    assert set(blind.get_json()["error"]["so_numbers"]) == {SANTINO_SO, GYM_SO}

    # Delete exactly one SO.
    single = client.delete(
        f"/api/v1/order-fulfillment/order-match/{run_id}?so_number={SANTINO_SO}",
        headers=headers,
    )
    assert single.status_code == 200, single.get_data(as_text=True)

    after_delete = _run_detail(client, headers, run_id)
    # (ii) the other SO's lines and value survived
    assert _so_numbers(after_delete) == {GYM_SO}
    assert float(after_delete["so_net_amount"]) == pytest.approx(GYM_VALUE)

    # Re-upload the same SO — must re-link, not create a rival run.
    again = _match(client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE))
    assert again.status_code == 200, again.get_data(as_text=True)

    restored = _run_detail(client, headers, again.get_json()["data"]["run_id"])
    # (i) no spurious MISSING_ON_SO
    assert int(restored["missing_count"]) == 0, restored["rows"]
    assert not [
        r for r in restored["rows"] if str(r.get("status")) == "MISSING_ON_SO"
    ]
    # (ii) both SOs present again
    assert _so_numbers(restored) == {SANTINO_SO, GYM_SO}
    # (iii) total order value correct and non-zero
    assert float(restored["so_net_amount"]) == pytest.approx(FO_VALUE)
    assert float(restored["fo_exmill_value"]) == pytest.approx(FO_VALUE)
    assert float(restored["delta_value"]) == pytest.approx(0.0)

    runs = client.get(
        "/api/v1/order-fulfillment/order-match/list", headers=headers
    ).get_json()["data"]["runs"]
    assert len([r for r in runs if r["filled_order_id"] == fo_id]) == 1


def test_deleting_last_so_removes_the_run(env):
    client, headers, fo_id, _db = env
    resp = _match(client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    run_id = resp.get_json()["data"]["run_id"]

    deleted = client.delete(
        f"/api/v1/order-fulfillment/order-match/{run_id}?so_number={SANTINO_SO}",
        headers=headers,
    )
    assert deleted.status_code == 200, deleted.get_data(as_text=True)
    assert (
        client.get(
            f"/api/v1/order-fulfillment/order-match/{run_id}", headers=headers
        ).status_code
        == 404
    )
    # SO index freed → same SO uploads cleanly again.
    again = _match(client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE))
    assert again.status_code == 200, again.get_data(as_text=True)


def test_repair_script_consolidates_duplicate_runs_for_one_fo(env):
    client, headers, fo_id, db_path = env
    first = _match(client, headers, fo_id, _pack(SANTINO_SO, SANTINO_PRODUCT, 100, SANTINO_VALUE))
    assert first.status_code == 200, first.get_data(as_text=True)
    run_id = first.get_json()["data"]["run_id"]

    # Simulate the damaged production shape: a rival run for the same FO that
    # only knows the re-uploaded SO, plus a stale SO index claim.
    from app.services import fo_so_match_db as matchdb

    conn = sqlite3.connect(db_path)
    matchdb.ensure_schema(conn)
    run = matchdb.get_match_run(conn, run_id, user_id=None)
    user_id = int(run["user_id"])
    conn.execute(
        """
        INSERT INTO fo_so_match_runs (
            user_id, filled_order_id, distributor_id, distributor_name,
            category, season, so_qty, so_net_amount, missing_count,
            rows_json, so_line_detail_json, created_at
        ) VALUES (?, ?, 1, 'Bernina Textiles', 'Bath', 'AW26', 50, ?, 1, '[]', ?, '2026-01-01')
        """,
        (
            user_id,
            fo_id,
            GYM_VALUE,
            __import__("json").dumps(
                [
                    {
                        "so_number": GYM_SO,
                        "product_name": GYM_PRODUCT,
                        "product_detail": GYM_PRODUCT,
                        "qty": 50,
                        "net_amount": GYM_VALUE,
                    }
                ]
            ),
        ),
    )
    conn.execute(
        "INSERT INTO fo_so_match_so_index (so_number, run_id, user_id, filled_order_id, created_at)"
        " VALUES ('999999999', 987654, ?, ?, '2026-01-01')",
        (user_id, fo_id),
    )
    conn.commit()
    conn.close()

    from scripts import repair_fo_so_match as repair

    assert repair.main(["--db", db_path, "--filled-order-id", str(fo_id)]) == 0
    conn = sqlite3.connect(db_path)
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM fo_so_match_runs WHERE filled_order_id = ?", (fo_id,)
        ).fetchone()[0]
        == 2
    ), "dry run must not change anything"
    conn.close()

    assert repair.main(["--db", db_path, "--filled-order-id", str(fo_id), "--apply"]) == 0

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, so_net_amount, missing_count FROM fo_so_match_runs WHERE filled_order_id = ?",
        (fo_id,),
    ).fetchall()
    assert len(rows) == 1
    assert float(rows[0][1]) == pytest.approx(FO_VALUE)
    assert int(rows[0][2]) == 0
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM fo_so_match_so_index WHERE run_id = 987654"
        ).fetchone()[0]
        == 0
    )
    # Idempotent.
    conn.close()
    assert repair.main(["--db", db_path, "--filled-order-id", str(fo_id), "--apply"]) == 0
    conn = sqlite3.connect(db_path)
    rows2 = conn.execute(
        "SELECT id, so_net_amount, missing_count FROM fo_so_match_runs WHERE filled_order_id = ?",
        (fo_id,),
    ).fetchall()
    conn.close()
    assert rows2 == rows

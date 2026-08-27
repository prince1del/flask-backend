"""Bed SO Pack lines must reach the FO match — Bernina AW26 "SO qty 0" incident.

The real pack (`fwdrfa0385bedsheetordersjun26forcashpayment1.zip`, 27 SO PDFs)
parsed perfectly — 675 line rows, 25 480 pcs, ₹2.98 Cr — yet the saved match
showed SO qty 0, SO Net 0 and all 41 FO buckets MISSING_ON_SO, because
`build_so_buckets_from_line_detail` fed the *long* wording
("BLUMEN 1+2 DB SET 224X254 8136LBL 104TC") into the Brand × Size teaching maps.
The set-type lookup is anchored at the end of the string, so the size never
resolved and every line was folded into "Others", which the compare ignores.

Customer data stays out of the repo: the always-on tests use synthetic lines in
the same shape as his PDFs, and the end-to-end test against the real ZIP is
skipped unless that file is present on the machine running the tests.
"""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest

import filled_orders_db as fodb

REAL_PACK = Path(
    r"g:\My Drive\2026-2027\Oder Management\AW26 order\Bedsheet\SO AW 26\Bernina"
    r"\fwdrfa0385bedsheetordersjun26forcashpayment1.zip"
)

# Exactly the shape of his SO PDF lines: short resolvable product_name plus a
# product_detail with size / design / colour / TC appended.
BED_LINES = [
    {
        "so_number": "102876136",
        "material_code": "BS02DBBLUMN8136LBL",
        "product_name": "BLUMEN 1+2 DB SET",
        "product_detail": "BLUMEN 1+2 DB SET 224X254 8136LBL 104TC",
        "qty": 36.0,
        "net_amount": 26420.0,
        "gst_amount": 1321.0,
        "total_amount": 27741.0,
    },
    {
        "so_number": "102876136",
        "material_code": "BS02SBBLUMN8136LBL",
        "product_name": "BLUMEN 1+1 SB SET",
        "product_detail": "BLUMEN 1+1 SB SET 140X224 8136LBL 104TC",
        "qty": 16.0,
        "net_amount": 7222.56,
        "gst_amount": 361.13,
        "total_amount": 7583.69,
    },
]
BED_QTY = sum(r["qty"] for r in BED_LINES)
BED_NET = round(sum(r["net_amount"] for r in BED_LINES), 2)


def _pack(lines: list[dict], *, source: str = "bernina_pack.zip") -> dict:
    return {
        "meta": {"source_filename": source},
        "line_detail": [dict(r) for r in lines],
        "consolidated": [],
        "so_summary": [],
    }


# ------------------------------------------------------- unit: bucketing


def test_bed_so_lines_resolve_brand_and_size_from_short_wording():
    from app.services.fo_so_match_lab import (
        build_so_buckets_from_line_detail,
        resolve_so_line_brand_size,
    )

    brand, size, _src = resolve_so_line_brand_size(BED_LINES[0])
    assert brand == "Blumen"
    assert size == "Double Bedsheet"

    built = build_so_buckets_from_line_detail(BED_LINES)
    assert built["line_count"] == 2, built
    assert float(built["others_qty"]) == 0.0
    assert float(built["total_qty"]) == pytest.approx(BED_QTY)
    assert float(built["total_value"]) == pytest.approx(BED_NET)


def test_towel_detail_wording_still_resolves():
    """The long detail wording must keep winning where the size lives there."""
    from app.services.fo_so_match_lab import resolve_so_line_brand_size

    brand, size, _src = resolve_so_line_brand_size(
        {
            "product_name": "SANTINO PRE DYED 2PC",
            "product_detail": "SANTINO PRE DYED 2PC 40X60CM ASST12 AW26",
            "qty": 12,
        }
    )
    assert brand, "towel collection must still resolve"
    assert size, "towel product type must still resolve"


def test_unknown_wording_keeps_its_qty_and_value_in_the_compare():
    """No SO line may vanish: unmapped wording still enters the buckets."""
    from app.services.fo_so_match_lab import build_so_buckets_from_line_detail

    built = build_so_buckets_from_line_detail(
        [
            {
                "so_number": "999",
                "product_name": "ZZZ MYSTERY THING",
                "product_detail": "ZZZ MYSTERY THING 1X1",
                "qty": 25.0,
                "net_amount": 500.0,
            }
        ]
    )
    assert built["line_count"] == 1
    assert int(built["unmapped_line_count"]) == 1
    assert float(built["total_qty"]) == pytest.approx(25.0)
    assert float(built["total_value"]) == pytest.approx(500.0)


# ------------------------------------------------------------ end to end


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "bed_bucketing.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "bed-bucketing-test-key")

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
        "Bernina International P Ltd", firm_nick_name="BND", workspace_id="ws-1"
    )

    login = client.post(
        "/api/v1/auth/login", json={"username": "bd_bernina", "password": "pass123"}
    )
    assert login.status_code == 200, login.get_data(as_text=True)
    headers = {"Authorization": f"Bearer {login.get_json()['data']['access_token']}"}

    def make_fo(items: list[tuple[str, str, str, float, float]]) -> int:
        conn = sqlite3.connect(str(db_path))
        fodb.ensure_schema(conn)
        fo_id = fodb.create_filled_order(
            conn,
            user_id,
            distributor_id,
            "Bernina International P Ltd",
            "Bedsheet",
            "AW26",
            source_filename="BND.xlsx",
        )
        for item_key, brand, size, qty, ex_mill in items:
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

    return {
        "client": client,
        "headers": headers,
        "user_id": user_id,
        "make_fo": make_fo,
        "db_path": str(db_path),
    }


def _match(client, headers, fo_id, pack, **extra):
    body = {
        "filled_order_id": fo_id,
        "so_pack": pack,
        "so_buyer_label": "Bernina International P Ltd",
    }
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


def test_bed_pack_upload_fills_so_qty_and_value(env):
    """FO + bed SO pack → non-zero SO qty / Net / final value, no false misses."""
    fo_id = env["make_fo"](
        [
            ("BLUMEN|DOUBLE BEDSHEET", "Blumen", "Double Bedsheet", 36, 733.9),
            ("BLUMEN|SINGLE BEDSHEET", "Blumen", "Single Bedsheet", 16, 451.41),
        ]
    )
    resp = _match(env["client"], env["headers"], fo_id, _pack(BED_LINES))
    assert resp.status_code == 200, resp.get_data(as_text=True)

    run = _run_detail(env["client"], env["headers"], resp.get_json()["data"]["run_id"])
    assert float(run["so_qty"]) == pytest.approx(BED_QTY)
    assert float(run["so_net_amount"]) == pytest.approx(BED_NET)
    assert int(run["missing_count"]) == 0, run["rows"]
    assert int(run["match_count"]) + int(run["mismatch_count"]) == 2
    final_total = sum(
        float(v.get("total") or 0) for v in (run.get("so_totals") or {}).values()
    )
    assert final_total > 0


def test_reupload_attaches_when_run_shows_no_so_even_if_content_identical(env):
    """The "already in system / no change detected" gate must not block a rebuild."""
    fo_id = env["make_fo"](
        [
            ("BLUMEN|DOUBLE BEDSHEET", "Blumen", "Double Bedsheet", 36, 733.9),
            ("BLUMEN|SINGLE BEDSHEET", "Blumen", "Single Bedsheet", 16, 451.41),
        ]
    )
    first = _match(env["client"], env["headers"], fo_id, _pack(BED_LINES))
    assert first.status_code == 200, first.get_data(as_text=True)
    run_id = first.get_json()["data"]["run_id"]

    # Production shape: lines are in the run, the match shows nothing.
    conn = sqlite3.connect(env["db_path"])
    conn.execute(
        "UPDATE fo_so_match_runs SET so_qty = 0, so_net_amount = 0, "
        "match_count = 0, mismatch_count = 0, missing_count = 2, rows_json = '[]' "
        "WHERE id = ?",
        (run_id,),
    )
    conn.commit()
    conn.close()

    again = _match(env["client"], env["headers"], fo_id, _pack(BED_LINES))
    assert again.status_code == 200, again.get_data(as_text=True)
    note = again.get_json()["data"].get("revision_note") or ""
    assert "rebuilt" in note.lower() or float(
        again.get_json()["data"]["run"]["so_qty"]
    ) > 0

    fixed = _run_detail(env["client"], env["headers"], run_id)
    assert float(fixed["so_qty"]) == pytest.approx(BED_QTY), "no doubling, no zero"
    assert float(fixed["so_net_amount"]) == pytest.approx(BED_NET)
    assert int(fixed["missing_count"]) == 0


def test_healthy_run_reupload_still_reports_no_change(env):
    fo_id = env["make_fo"](
        [
            ("BLUMEN|DOUBLE BEDSHEET", "Blumen", "Double Bedsheet", 36, 733.9),
            ("BLUMEN|SINGLE BEDSHEET", "Blumen", "Single Bedsheet", 16, 451.41),
        ]
    )
    first = _match(env["client"], env["headers"], fo_id, _pack(BED_LINES))
    assert first.status_code == 200, first.get_data(as_text=True)
    run_id = first.get_json()["data"]["run_id"]
    before = _run_detail(env["client"], env["headers"], run_id)

    again = _match(env["client"], env["headers"], fo_id, _pack(BED_LINES))
    assert again.status_code == 409, again.get_data(as_text=True)
    assert again.get_json()["error"]["code"] == "so_already_in_system"

    after = _run_detail(env["client"], env["headers"], run_id)
    assert float(after["so_qty"]) == pytest.approx(float(before["so_qty"]))
    assert float(after["so_net_amount"]) == pytest.approx(float(before["so_net_amount"]))


def test_unreadable_pack_is_refused_with_a_clear_message(env):
    fo_id = env["make_fo"](
        [("BLUMEN|DOUBLE BEDSHEET", "Blumen", "Double Bedsheet", 36, 733.9)]
    )
    pack = _pack(
        [
            {
                "so_number": "102876136",
                "product_name": "BLUMEN 1+2 DB SET",
                "product_detail": "BLUMEN 1+2 DB SET",
                "qty": 0,
                "net_amount": 0,
                "total_amount": 0,
            }
        ]
    )
    resp = _match(env["client"], env["headers"], fo_id, pack)
    assert resp.status_code == 400, resp.get_data(as_text=True)
    err = resp.get_json()["error"]
    assert err["code"] in ("so_pack_missing_line_detail", "so_pack_unreadable")
    assert "no sales order lines" in err["message"].lower()


def test_unreflected_run_heals_on_plain_order_desk_read(env):
    fo_id = env["make_fo"](
        [
            ("BLUMEN|DOUBLE BEDSHEET", "Blumen", "Double Bedsheet", 36, 733.9),
            ("BLUMEN|SINGLE BEDSHEET", "Blumen", "Single Bedsheet", 16, 451.41),
        ]
    )
    first = _match(env["client"], env["headers"], fo_id, _pack(BED_LINES))
    assert first.status_code == 200, first.get_data(as_text=True)
    run_id = first.get_json()["data"]["run_id"]

    conn = sqlite3.connect(env["db_path"])
    conn.execute(
        "UPDATE fo_so_match_runs SET so_qty = 0, so_net_amount = 0, "
        "match_count = 0, missing_count = 2, rows_json = '[]' WHERE id = ?",
        (run_id,),
    )
    conn.commit()
    conn.close()

    listed = env["client"].get(
        "/api/v1/order-fulfillment/order-match/list", headers=env["headers"]
    )
    assert listed.status_code == 200, listed.get_data(as_text=True)
    healed = [r for r in listed.get_json()["data"]["runs"] if r["id"] == run_id][0]
    assert float(healed["so_qty"]) == pytest.approx(BED_QTY)
    assert float(healed["so_net_amount"]) == pytest.approx(BED_NET)
    assert int(healed["missing_count"]) == 0


@pytest.mark.skipif(
    not REAL_PACK.exists(),
    reason="real Bernina SO pack not present on this machine",
)
def test_real_bernina_pack_end_to_end(env):
    """Full flow on his actual 27-PDF pack: parse → match → non-zero totals."""
    from app.services.so_pack_consolidate import analyze_so_pack

    data = analyze_so_pack(REAL_PACK.read_bytes(), REAL_PACK.name)
    lines = data.get("line_detail") or []
    assert len(lines) > 500, "parser must return the SO line detail"
    pack_qty = sum(float(r.get("qty") or 0) for r in lines)
    assert pack_qty > 0

    # FO built from the pack's own Brand × Size buckets, so the compare has a
    # real counterpart without shipping the customer's FO workbook.
    from app.services.fo_so_match_lab import build_so_buckets_from_line_detail

    built = build_so_buckets_from_line_detail(lines)
    assert float(built["others_qty"]) == 0.0, "no SO line may fall into Others"
    items = []
    for (brand_key, _size_key), bucket in built["buckets"].items():
        rate = (bucket["value"] / bucket["qty"]) if bucket["qty"] else 0.0
        items.append(
            (
                f"{brand_key}|{bucket['size']}",
                bucket["brand"],
                bucket["size"],
                bucket["qty"],
                round(rate, 4),
            )
        )
    fo_id = env["make_fo"](items)

    resp = _match(env["client"], env["headers"], fo_id, _pack(lines, source=REAL_PACK.name))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    run = _run_detail(env["client"], env["headers"], resp.get_json()["data"]["run_id"])
    assert float(run["so_qty"]) == pytest.approx(pack_qty, rel=1e-6)
    assert float(run["so_net_amount"]) > 0
    assert int(run["missing_count"]) == 0, "every FO bucket has its SO counterpart"
    assert len(run.get("so_totals") or {}) == 27, "all 27 Sales Orders visible"

    # And the re-upload gate: wipe the saved match, upload the same pack again.
    conn = sqlite3.connect(env["db_path"])
    conn.execute(
        "UPDATE fo_so_match_runs SET so_qty = 0, so_net_amount = 0, rows_json = '[]', "
        "match_count = 0, missing_count = 41 WHERE id = ?",
        (int(run["id"]),),
    )
    conn.commit()
    conn.close()
    again = _match(
        env["client"], env["headers"], fo_id, _pack(lines, source=REAL_PACK.name)
    )
    assert again.status_code == 200, again.get_data(as_text=True)
    rebuilt = _run_detail(env["client"], env["headers"], int(run["id"]))
    assert float(rebuilt["so_qty"]) == pytest.approx(pack_qty, rel=1e-6)

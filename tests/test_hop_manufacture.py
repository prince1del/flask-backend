"""Manufacturing: raw-material list, plan, manufacture (stock moves + cost), undo, block on short stock."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_app_settings as S, hop_manufacture as M
from app.hop_db import connect
from app.hop_ops import create_product
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def db(tmp_path: Path):
    p = str(tmp_path / "mfg.sqlite3")
    ensure_hop_schema(p)
    with connect(p) as conn:
        cur = create_product(conn, WS, {"name": "Curtain", "unit": "PCS", "stock_qty": 0})
        fab = create_product(conn, WS, {"name": "Fabric", "unit": "MTR", "stock_qty": 20, "purchase_price": 200})
        tape = create_product(conn, WS, {"name": "Tape", "unit": "MTR", "stock_qty": 3, "purchase_price": 10})
        conn.commit()
    return p, cur["id"], fab["id"], tape["id"]


def _stock(conn, pid):
    return conn.execute("SELECT stock_qty FROM hop_products WHERE id=?", (pid,)).fetchone()[0]


def test_bom_rules(db):
    p, cur, fab, tape = db
    with connect(p) as conn:
        with pytest.raises(ValueError):
            M.save_bom(conn, WS, cur, [{"raw_product_id": cur, "qty": 1}])
        with pytest.raises(ValueError):
            M.save_bom(conn, WS, cur, [{"raw_product_id": fab, "qty": 0}])
        b = M.save_bom(conn, WS, cur, [{"raw_product_id": fab, "qty": 2.5}, {"raw_product_id": tape, "qty": 1}])
        assert b["cost_per_piece"] == 510 and len(b["raw"]) == 2
        with pytest.raises(ValueError):  # fabric made from curtain → loop
            M.save_bom(conn, WS, fab, [{"raw_product_id": cur, "qty": 1}])


def test_plan_manufacture_and_undo(db):
    p, cur, fab, tape = db
    with connect(p) as conn:
        M.save_bom(conn, WS, cur, [{"raw_product_id": fab, "qty": 2.5}, {"raw_product_id": tape, "qty": 1}])
        pl = M.plan(conn, WS, cur, 4, extra_cost=200)
        assert pl["can_make_now"] == 3  # tape 3 m limits it
        assert pl["raw_cost"] == 2040 and pl["unit_cost"] == 560
        assert any("Tape" in w and "short 1" in w for w in pl["warnings"])
        out = M.manufacture(conn, WS, {"product_id": cur, "qty": 2, "extra_cost": 100, "entry_date": "2026-11-17"})
        assert out["unit_cost"] == 560 and out["warnings"] == []
        assert (_stock(conn, cur), _stock(conn, fab), _stock(conn, tape)) == (2, 15, 1)
        # first time made → cost price set for profit reports
        assert conn.execute("SELECT purchase_price FROM hop_products WHERE id=?", (cur,)).fetchone()[0] == 560
        assert M.list_entries(conn, WS)[0]["used"][0]["name"] == "Fabric"
        M.undo(conn, WS, out["id"])
        assert (_stock(conn, cur), _stock(conn, fab), _stock(conn, tape)) == (0, 20, 3)
        assert M.list_entries(conn, WS) == []
        with pytest.raises(ValueError):
            M.undo(conn, WS, out["id"])


def test_short_stock_block_setting(db):
    p, cur, fab, tape = db
    with connect(p) as conn:
        M.save_bom(conn, WS, cur, [{"raw_product_id": tape, "qty": 1}])
        out = M.manufacture(conn, WS, {"product_id": cur, "qty": 5})  # warn mode: allowed
        assert out["warnings"] and _stock(conn, tape) == -2
        S.save(conn, WS, {"short_stock": "block"})
        with pytest.raises(ValueError):
            M.manufacture(conn, WS, {"product_id": cur, "qty": 5})


def test_ask_nexora_summary(db):
    p, cur, fab, tape = db
    from app.services.hop_ai import books_answer

    with connect(p) as conn:
        M.save_bom(conn, WS, cur, [{"raw_product_id": fab, "qty": 2.5}])
        out = books_answer(conn, WS, "manufacturing", "curt")
        assert out["items"][0]["item"] == "Curtain" and out["items"][0]["can_make_now"] == 8


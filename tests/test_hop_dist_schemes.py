"""Dated CD and incentive schemes on a made-up firm: a new CD scheme never rewrites old invoices; incentive progress,
alerts; per-article margins from the company sheet's % columns."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from app import hop_dist_company as CO
from app import hop_dist_extra as X
from app import hop_dist_schemes as SC
from app import hop_distributor as D
from app import hop_dist_margins as MG
from app.hop_db import connect
from app.hop_doc_preview import create_manual_party_document as create
from app.hop_ops import create_vendor
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def firm(tmp_path: Path):
    path = str(tmp_path / "sc.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        a = create_vendor(conn, WS, {"company": "Gamma Mills"})
        D.set_principal(conn, WS, a["id"], True)
        from app import hop_dist_terms as T

        T.save_terms(conn, WS, a["id"], {"cash_discount_pct": 1, "credit_days": 30})
    return path, a["id"]


def _ci(conn, vid, day: date, no: str, amt: float, cat_item: str = "Gamma Towel"):
    return create(conn, WS, {"txn_type": 2, "party_type": "vendor", "vendor_id": vid, "txn_date": day.isoformat(),
                             "party_bill_no": no, "lines": [{"item_name": cat_item, "qty": 1, "rate": amt, "tax_pct": 0}]})


def test_new_cd_scheme_keeps_old_invoices(firm):
    path, vid = firm
    today = date.today()
    with connect(path) as conn:
        _ci(conn, vid, today - timedelta(days=60), "OLD-1", 10000)      # before the scheme: old single CD 1 %
        _ci(conn, vid, today - timedelta(days=2), "NEW-1", 10000)       # inside the scheme
        SC.save(conn, WS, vid, {"kind": "cd", "title": "Q3 CD", "from_date": (today - timedelta(days=10)).isoformat(),
                                "slabs": [{"days": 15, "pct": 2}, {"days": 7, "pct": 3}]})
        cd = CO.cash_discount(conn, WS, vid)
        by = {r["ci_no"]: r for r in cd["rows"]}
        assert by["OLD-1"]["scheme"] == "Old CD % (no dates)" and by["OLD-1"]["cd_pct"] == 1
        assert by["NEW-1"]["scheme"] == "Q3 CD" and by["NEW-1"]["cd_pct"] == 3 and by["NEW-1"]["status"] == "open"
        assert by["NEW-1"]["cd"] == 300 and by["NEW-1"]["pay_by"] == (today + timedelta(days=5)).isoformat()
        assert any("NEW-1" in a["text"] for a in SC.alerts(conn, WS, vid))


def test_cd_slab_moves_down_then_lost():
    inv = date(2026, 1, 1)
    rule = {"slabs": [{"days": 7, "pct": 3}, {"days": 15, "pct": 2}]}
    assert SC.cd_status(rule, 1000, inv, None, 1000, date(2026, 1, 10))["pct"] == 2          # past 7 days → 2 %
    assert SC.cd_status(rule, 1000, inv, None, 1000, date(2026, 1, 20))["status"] == "lost"
    assert SC.cd_status(rule, 1000, inv, date(2026, 1, 5), 0, date(2026, 2, 1))["status"] == "earned"


def test_incentive_progress_and_alerts(firm):
    path, vid = firm
    today = date.today()
    with connect(path) as conn:
        _ci(conn, vid, today - timedelta(days=3), "CI-1", 90000)
        s = SC.save(conn, WS, vid, {"kind": "incentive", "title": "Diwali target", "from_date": (today - timedelta(days=20)).isoformat(),
                                    "to_date": (today + timedelta(days=5)).isoformat(),
                                    "slabs": [{"target": 100000, "pct": 1}, {"target": 150000, "pct": 1.5}]})
        p = SC.progress(conn, WS, s)
        assert p["bought"] == 90000 and p["reached"] is None and p["gap"] == 10000 and p["status"] == "running"
        assert any("10,000 more" in a["text"] for a in SC.alerts(conn, WS, vid))
        _ci(conn, vid, today - timedelta(days=1), "CI-2", 20000)
        p = SC.progress(conn, WS, s)
        assert p["reached"]["target"] == 100000 and p["earned_now"] == 1100 and p["next"]["target"] == 150000
        with pytest.raises(ValueError):
            SC.save(conn, WS, vid, {"kind": "incentive", "from_date": today.isoformat(), "slabs": [{"target": 1, "pct": 1}]})


def test_sheet_percent_columns_become_article_margins(tmp_path):
    path = str(tmp_path / "mg.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        v = create_vendor(conn, WS, {"company": "Delta Home"})
        rows = X.price_list_preview(conn, WS, [{"name": "Delta Double Sheet", "code": "DD-1", "mrp": 799, "gst_pct": 5,
                                               "details": {"Retailer Margin": 0.3, "AWD Mark up on Exmill": 0.18}}])["rows"]
        mg = rows[0]["margin"]
        assert mg["terms"]["gst_out"] == "retail"
        assert mg["prices"]["ptr"] == 532.67 and mg["prices"]["ex_mill"] == 451.41
        done = X.price_list_apply(conn, WS, rows, vendor_id=v["id"])
        assert done["created"] == 1 and done["margins"] == 1
        r = conn.execute("SELECT mg_retail_pct, mg_awd_pct, mg_gst_out, mg_from FROM hop_products WHERE code='DD-1'").fetchone()
        assert (r[0], r[1], r[2], r[3]) == (30, 18, "retail", "own")
        assert MG.chain(799, 5, {"retail_pct": 30, "awd_pct": 18, "gst_out": "retail"})["ex_mill"] == 451.41

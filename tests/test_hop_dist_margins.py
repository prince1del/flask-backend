"""Terms book: the margin chain per article, defaults per company + category (made-up companies)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_dist_extra as X
from app import hop_dist_margins as M
from app import hop_distributor as D
from app.hop_db import connect
from app.hop_ops import create_vendor
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def firm(tmp_path: Path):
    path = str(tmp_path / "m.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        M.ensure_schema(conn)
        a = create_vendor(conn, WS, {"company": "Alpha Mills"})
        b = create_vendor(conn, WS, {"company": "Beta Textiles"})
        D.set_principal(conn, WS, a["id"], True)
        D.set_principal(conn, WS, b["id"], True)
        s1 = X.save_article(conn, WS, {"name": "Alpha Sheet One", "category": "Bed linen", "mrp": 1000, "gst_pct": 5,
                                       "vendor_id": a["id"]})
        s2 = X.save_article(conn, WS, {"name": "Alpha Sheet Two", "category": "Bed linen", "mrp": 2000, "gst_pct": 5,
                                       "vendor_id": a["id"]})
        t = X.save_article(conn, WS, {"name": "Beta Towel", "category": "Bed linen", "mrp": 500, "gst_pct": 5,
                                      "vendor_id": b["id"], "purchase_price": 100, "selling_price": 300})
    return path, {"a": a["id"], "b": b["id"], "s1": s1["id"], "s2": s2["id"], "towel": t["id"]}


def test_chain_shapes():
    # GST out before the retailer margin, then the distributor mark-up
    p = M.chain(1050, 5, {"retail_pct": 30, "gst_out": "retail", "awd_pct": 25})
    assert p["ptr"] == 700 and p["ex_mill"] == 560 and p["my_margin"] == 140
    # mark-down straight from MRP, GST out before the distributor mark-up
    p = M.chain(1000, 5, {"retail_pct": 40, "gst_out": "awd", "awd_pct": 20})
    assert p["ptr"] == 600 and p["ex_mill"] == round(600 / 1.05 / 1.2, 2)
    # a perceived (consumer) discount first; no distributor % → no ex-mill
    p = M.chain(1000, 5, {"perceived_pct": 40, "retail_pct": 30, "gst_out": "none"})
    assert p["selling"] == 600 and p["ptr"] == 420 and "ex_mill" not in p
    assert M.chain(0, 5, {"retail_pct": 30}) is None and M.chain(100, 5, {}) is None


def test_defaults_per_company_and_category_and_own_article(firm):
    path, ids = firm
    with connect(path) as conn:
        # company A bed linen: mark-down 30% from MRP + 20% mark-up; company B bed linen: another rule
        b = M.save_default(conn, WS, ids["a"], "Bed linen", {"retail_pct": 30, "gst_out": "awd", "awd_pct": 20,
                                                             "season": "AW26"})
        M.save_default(conn, WS, ids["b"], "Bed linen", {"retail_pct": 15, "gst_out": "none", "awd_pct": 18})
        rows = {r["id"]: r for r in b["articles"]}
        assert rows[ids["s1"]]["from"] == "default" and rows[ids["s1"]]["prices"]["ptr"] == 700
        assert M.following(conn, WS, ids["a"], "Bed linen") == 2
        # one article has its own % (like one bedsheet with a different margin)
        M.save_article(conn, WS, ids["s2"], {"retail_pct": 28, "awd_pct": 15, "gst_out": "awd"})
        assert M.following(conn, WS, ids["a"], "Bed linen") == 1
        # a new default reaches the article that follows it, never the one with its own terms
        b = M.save_default(conn, WS, ids["a"], "Bed linen", {"retail_pct": 35})
        rows = {r["id"]: r for r in b["articles"]}
        assert rows[ids["s1"]]["prices"]["ptr"] == 650 and rows[ids["s1"]]["terms"]["season"] == "AW26"
        assert rows[ids["s2"]]["from"] == "own" and rows[ids["s2"]]["prices"]["ptr"] == 1440
        # company B's own book shows only its article, with checks against the saved prices
        bb = M.book(conn, WS, ids["b"])
        assert [r["name"] for r in bb["articles"]] == ["Beta Towel"] and bb["articles"][0]["checks"]
        # back to the default
        M.save_article(conn, WS, ids["s2"], {"follow_default": True})
        assert M.following(conn, WS, ids["a"], "Bed linen") == 2


def test_apply_saves_only_ticked_and_validation(firm):
    path, ids = firm
    with connect(path) as conn:
        M.save_default(conn, WS, ids["a"], "Bed linen", {"retail_pct": 30, "gst_out": "none", "awd_pct": 25})
        out = M.apply(conn, WS, ids["a"], [ids["s1"]])
        assert out["updated"] == 1
        r = conn.execute("SELECT selling_price, purchase_price FROM hop_products WHERE id=?", (ids["s1"],)).fetchone()
        assert (r[0], r[1]) == (700, 560)
        r2 = conn.execute("SELECT selling_price FROM hop_products WHERE id=?", (ids["s2"],)).fetchone()
        assert not r2[0]   # not ticked → not changed
        with pytest.raises(ValueError):
            M.save_default(conn, WS, ids["a"], "Bed linen", {"retail_pct": 120})
        with pytest.raises(ValueError):
            M.save_default(conn, WS, ids["a"], "Towels", {"awd_pct": 10})   # retailer % is needed
        with pytest.raises(LookupError):
            M.book(conn, WS, 99999)
        assert "Alpha Mills" in M.summary_text(conn, WS)


def test_add_company_and_article_needs_company(tmp_path):
    """Owner 21-11: a company (like B or C) is added with its details; a new article must say its company."""
    path = str(tmp_path / "c.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        M.ensure_schema(conn)
        assert "add the company" in M.need_company(conn, WS, {"name": "Sheet"})
        with pytest.raises(ValueError):
            M.add_company(conn, WS, {"company": "Gamma Home", "gst_no": "BAD"})
        g = M.add_company(conn, WS, {"company": "Gamma Home", "gst_no": "27ABCDE1234F1Z5", "state": "Maharashtra",
                                     "address": "1 Mill Road"})
        assert g["existed"] is False
        assert M.need_company(conn, WS, {"name": "Sheet"}) is None          # one company → it is taken
        again = M.add_company(conn, WS, {"company": "gamma home"})
        assert again["existed"] and again["id"] == g["id"]
        M.add_company(conn, WS, {"company": "Delta Linen"})
        assert "Pick the company" in M.need_company(conn, WS, {"name": "Sheet"})
        assert M.need_company(conn, WS, {"name": "Sheet", "vendor_id": g["id"]}) is None
        r = conn.execute("SELECT gst_no, state, is_principal FROM hop_vendors WHERE id=?", (g["id"],)).fetchone()
        assert (r[0], r[1], r[2]) == ("27ABCDE1234F1Z5", "Maharashtra", 1)

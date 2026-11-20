"""Distributor with many companies: own terms + price rule per company and per article (made-up firm)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import hop_dist_extra as X
from app import hop_dist_terms as T
from app import hop_distributor as D
from app.hop_db import connect
from app.hop_ops import create_vendor
from app.hop_schema import HOP_WORKSPACE_ID as WS, ensure_hop_schema


@pytest.fixture
def firm(tmp_path: Path):
    path = str(tmp_path / "t.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        T.ensure_schema(conn)
        a = create_vendor(conn, WS, {"company": "Alpha Mills"})
        b = create_vendor(conn, WS, {"company": "Beta Textiles"})
        D.set_principal(conn, WS, a["id"], True)
        D.set_principal(conn, WS, b["id"], True)
        t = X.save_article(conn, WS, {"name": "Alpha Towel", "mrp": 300, "purchase_price": 150, "selling_price": 160,
                                      "vendor_id": a["id"]})
        s = X.save_article(conn, WS, {"name": "Beta Sheet", "mrp": 1000, "purchase_price": 500, "selling_price": 650,
                                      "vendor_id": b["id"]})
        n = X.save_article(conn, WS, {"name": "Loose Pillow", "mrp": 200, "purchase_price": 90})
    return path, {"a": a["id"], "b": b["id"], "towel": t["id"], "sheet": s["id"], "pillow": n["id"]}


def test_rule_price():
    assert T.rule_price(100, 0, "markup", 10) == 110
    assert T.rule_price(90, 0, "margin", 10) == 100
    assert T.rule_price(0, 1000, "markdown", 25) == 750
    assert T.rule_price(0, 0, "markup", 10) is None


def test_terms_rules_check_and_apply(firm):
    path, ids = firm
    with connect(path) as conn:
        o = T.save_terms(conn, WS, ids["a"], {"payment_terms": "30 days", "credit_days": 30, "cash_discount_pct": 2,
                                              "price_rule": "markup", "rule_pct": 20, "terms_note": "Freight paid"})
        alpha = next(c for c in o["companies"] if c["id"] == ids["a"])
        assert alpha["rule"] == "markup" and alpha["rule_pct"] == 20 and alpha["credit_days"] == 30
        assert alpha["articles"] == 1 and alpha["off_rule"] == 1
        assert o["unassigned"] == 1          # the pillow has no company
        T.save_terms(conn, WS, ids["b"], {"price_rule": "markdown", "rule_pct": 30})
        chk = T.price_check(conn, WS, ids["a"])
        assert [r["suggested"] for r in chk["rows"]] == [180]     # 150 + 20%
        chk_b = T.price_check(conn, WS, ids["b"])
        assert chk_b["rows"][0]["suggested"] == 700               # 1000 - 30%
        # article's own rule wins over the company's
        X.save_article(conn, WS, {"id": ids["towel"], "price_rule": "margin", "price_rule_pct": 25})
        r = T.price_check(conn, WS, ids["a"])["rows"][0]
        assert r["suggested"] == 200 and r["own_rule"]
        # nothing changed until applied; apply only the ticked ones, history keeps the old price
        assert X.save_article(conn, WS, {"id": ids["towel"]})["selling_price"] == 160
        res = T.apply_prices(conn, WS, ids["a"], [ids["towel"]], user="Owner")
        assert res["updated"] == 1
        assert X.price_history(conn, WS, ids["towel"])[0]["new_value"] == 200
        assert T.price_check(conn, WS, ids["a"])["rows"] == []
        # assign the loose pillow to Beta
        T.assign_company(conn, WS, [ids["pillow"]], ids["b"])
        assert T.overview(conn, WS)["unassigned"] == 0
        with pytest.raises(ValueError):
            T.save_terms(conn, WS, ids["a"], {"price_rule": "double"})
        with pytest.raises(ValueError):
            T.save_terms(conn, WS, ids["a"], {"cash_discount_pct": 80})


def test_rule_capped_at_mrp_and_below_dp_warns(firm):
    path, ids = firm
    with connect(path) as conn:
        T.save_terms(conn, WS, ids["a"], {"price_rule": "markup", "rule_pct": 200})
        r = T.price_check(conn, WS, ids["a"])["rows"][0]
        assert r["suggested"] == 300 and "above MRP" in r["warn"][0]
        T.save_terms(conn, WS, ids["a"], {"price_rule": "markdown", "rule_pct": 60})
        r = T.price_check(conn, WS, ids["a"])["rows"][0]
        assert r["suggested"] == 120 and any("below DP" in w for w in r["warn"])


def test_one_company_owns_loose_articles(tmp_path: Path):
    path = str(tmp_path / "one.sqlite3")
    ensure_hop_schema(path)
    with connect(path) as conn:
        T.ensure_schema(conn)
        a = create_vendor(conn, WS, {"company": "Solo Brand"})
        D.set_principal(conn, WS, a["id"], True)
        X.save_article(conn, WS, {"name": "Solo Towel", "mrp": 200, "purchase_price": 100, "selling_price": 100})
        T.save_terms(conn, WS, a["id"], {"price_rule": "markup", "rule_pct": 10})
        o = T.overview(conn, WS)
        assert o["unassigned"] == 0 and o["companies"][0]["articles"] == 1
        assert T.price_check(conn, WS, a["id"])["rows"][0]["suggested"] == 110

"""Distributor: many companies (brands), each with its own terms and its own price rule.

A distributor can buy from several companies (principals). Each company has:
  - payment terms (text), credit days, cash discount %, lead time, a free terms note;
  - a **price rule** for the price to the retailer, worked out from the DP (buying price) or the MRP:
      markup   — price = DP × (1 + p%)            ("10% upar DP pe")
      margin   — price = DP ÷ (1 − p%)            (my margin is p% of the selling price)
      markdown — price = MRP × (1 − p%)           ("MRP se p% kam")
An article can carry its own rule (price_rule / price_rule_pct on hop_products) that wins over the company's.

Nothing changes a price by itself: ``price_check`` only lists what the rule says, ``apply_prices`` saves the
rows the user ticked (through ``hop_dist_extra.save_article``, so the price history keeps every change).
"""

from __future__ import annotations

import sqlite3
from typing import Any

RULES = ("markup", "margin", "markdown")
RULE_LABEL = {"markup": "Mark-up on DP", "margin": "Margin on selling price", "markdown": "Mark-down from MRP"}
_PCT_COL = {"markup": "markup_pct", "margin": "margin_pct", "markdown": "markdown_pct"}


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _s(v: Any) -> str:
    return str(v or "").strip()


def ensure_schema(conn: sqlite3.Connection) -> None:
    from app import hop_dist_extra as X
    from app import hop_distributor as D

    D.ensure_schema(conn)
    X.ensure_schema(conn)
    vcols = {r[1] for r in conn.execute("PRAGMA table_info(hop_vendors)")}
    for col, typ in (("price_rule", "TEXT"), ("credit_days", "INTEGER"), ("cash_discount_pct", "REAL"),
                     ("terms_note", "TEXT")):
        if col not in vcols:
            conn.execute(f"ALTER TABLE hop_vendors ADD COLUMN {col} {typ}")
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(hop_products)")}
    for col, typ in (("vendor_id", "INTEGER"), ("price_rule", "TEXT"), ("price_rule_pct", "REAL")):
        if col not in pcols:
            conn.execute(f"ALTER TABLE hop_products ADD COLUMN {col} {typ}")


def rule_price(dp: float, mrp: float, rule: str, pct: float) -> float | None:
    """Price to the retailer by the rule, rounded to ₹1. None when the needed price is missing."""
    p = _f(pct)
    if rule == "markup" and dp > 0:
        v = dp * (1 + p / 100)
    elif rule == "margin" and dp > 0 and p < 100:
        v = dp / (1 - p / 100)
    elif rule == "markdown" and mrp > 0 and p < 100:
        v = mrp * (1 - p / 100)
    else:
        return None
    return float(int(v + 0.5))


def _company_rule(c: dict) -> tuple[str | None, float | None]:
    rule = _s(c.get("price_rule"))
    if rule in RULES and c.get(_PCT_COL[rule]) is not None:
        return rule, _f(c.get(_PCT_COL[rule]))
    for r in RULES:   # older saves: only a % was set
        if c.get(_PCT_COL[r]) not in (None, ""):
            return r, _f(c.get(_PCT_COL[r]))
    return None, None


def _companies(conn, ws) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, company, gst_no, city, mobile, payment_terms, lead_time_days, credit_days, cash_discount_pct, "
        "terms_note, price_rule, margin_pct, markup_pct, markdown_pct FROM hop_vendors "
        "WHERE workspace_id=? AND is_principal=1 ORDER BY lower(company)", (ws,)).fetchall()]


def _articles(conn, ws) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, code, name, category, mrp, purchase_price, selling_price, vendor_id, price_rule, price_rule_pct "
        "FROM hop_products WHERE workspace_id=? AND COALESCE(status,'active')='active' "
        "AND (deleted_at IS NULL OR trim(deleted_at)='') ORDER BY lower(name)", (ws,)).fetchall()]


def _owner(a: dict, ids: set[int], only: int | None) -> int | None:
    """The company of an article; with one company only, an article without a company is that company's."""
    v = int(a.get("vendor_id") or 0)
    if v in ids:
        return v
    return only if not v or v not in ids else None


def overview(conn: sqlite3.Connection, ws: str) -> dict[str, Any]:
    """Every company I distribute for, with its terms, price rule and how many articles follow it."""
    ensure_schema(conn)
    comps = _companies(conn, ws)
    ids = {int(c["id"]) for c in comps}
    only = next(iter(ids)) if len(ids) == 1 else None
    arts = _articles(conn, ws)
    out = []
    for c in comps:
        rule, pct = _company_rule(c)
        mine = [a for a in arts if _owner(a, ids, only) == int(c["id"])]
        off = 0
        for a in mine:
            r, p = (a["price_rule"], a["price_rule_pct"]) if a.get("price_rule") in RULES else (rule, pct)
            want = rule_price(_f(a["purchase_price"]), _f(a["mrp"]), r or "", p or 0) if r else None
            if want is not None and abs(want - _f(a["selling_price"])) >= 1:
                off += 1
        out.append({**c, "rule": rule, "rule_pct": pct, "rule_label": RULE_LABEL.get(rule or ""),
                    "articles": len(mine), "own_rule_articles": sum(1 for a in mine if a.get("price_rule") in RULES),
                    "off_rule": off})
    unassigned = 0 if only else sum(1 for a in arts if _owner(a, ids, None) is None)
    return {"companies": out, "unassigned": unassigned, "rules": [{"key": k, "label": RULE_LABEL[k]} for k in RULES]}


def save_terms(conn: sqlite3.Connection, ws: str, vendor_id: int, p: dict) -> dict[str, Any]:
    """{payment_terms, credit_days, cash_discount_pct, lead_time_days, terms_note, price_rule, rule_pct}."""
    ensure_schema(conn)
    if not conn.execute("SELECT 1 FROM hop_vendors WHERE workspace_id=? AND id=?", (ws, int(vendor_id))).fetchone():
        raise LookupError("Company not found")
    sets, vals = [], []

    def num(key, col, kind=float, lo=0.0, hi=None):
        if key not in p:
            return
        v = p.get(key)
        if v in (None, ""):
            sets.append(f"{col}=?"); vals.append(None); return
        try:
            x = kind(float(v))
        except (TypeError, ValueError):
            raise ValueError(f"{key.replace('_', ' ')}: type a number")
        if x < lo or (hi is not None and x > hi):
            raise ValueError(f"{key.replace('_', ' ')} must be between {lo:g} and {hi:g}" if hi else
                             f"{key.replace('_', ' ')} cannot be below {lo:g}")
        sets.append(f"{col}=?"); vals.append(x)

    for key in ("payment_terms", "terms_note"):
        if key in p:
            sets.append(f"{key}=?"); vals.append(_s(p.get(key))[:500] or None)
    num("credit_days", "credit_days", int, 0, 365)
    num("lead_time_days", "lead_time_days", int, 0, 365)
    num("cash_discount_pct", "cash_discount_pct", float, 0, 50)
    if "price_rule" in p:
        rule = _s(p.get("price_rule"))
        if rule and rule not in RULES:
            raise ValueError("Price rule must be markup, margin or markdown")
        sets.append("price_rule=?"); vals.append(rule or None)
        if rule:
            num("rule_pct", _PCT_COL[rule], float, 0, 99 if rule != "markup" else 500)
    if not sets:
        raise ValueError("Nothing to save")
    from app.hop_dist_extra import _now
    conn.execute(f"UPDATE hop_vendors SET {', '.join(sets)}, updated_at=? WHERE workspace_id=? AND id=?",
                 [*vals, _now(), ws, int(vendor_id)])
    conn.commit()
    return overview(conn, ws)


def price_check(conn: sqlite3.Connection, ws: str, vendor_id: int) -> dict[str, Any]:
    """✨ For one company: every article, the price its rule gives, and what does not match. Nothing is saved."""
    ensure_schema(conn)
    comps = {int(c["id"]): c for c in _companies(conn, ws)}
    c = comps.get(int(vendor_id))
    if not c:
        raise LookupError("Company not found")
    only = next(iter(comps)) if len(comps) == 1 else None
    rule, pct = _company_rule(c)
    rows, missing = [], 0
    for a in _articles(conn, ws):
        if _owner(a, set(comps), only) != int(vendor_id):
            continue
        own = a.get("price_rule") in RULES
        r, p = (a["price_rule"], _f(a["price_rule_pct"])) if own else (rule, pct)
        dp, mrp, cur = _f(a["purchase_price"]), _f(a["mrp"]), _f(a["selling_price"])
        if not r:
            continue
        want = rule_price(dp, mrp, r, p or 0)
        if want is None:
            missing += 1
            continue
        warn = []
        if mrp and want > mrp:
            warn.append(f"rule gives ₹{want:,.0f}, above MRP ₹{mrp:,.0f} — kept at MRP")
            want = mrp
        if dp and want < dp:
            warn.append(f"below DP ₹{dp:,.0f} — you would sell at a loss")
        if abs(want - cur) < 1 and not warn:
            continue
        rows.append({"id": a["id"], "code": a.get("code"), "name": a["name"], "category": a.get("category"),
                     "dp": dp or None, "mrp": mrp or None, "current": cur or None, "suggested": want,
                     "rule": r, "rule_pct": p, "rule_label": RULE_LABEL[r], "own_rule": own,
                     "margin_pct": round((want - dp) / want * 100, 1) if want and dp else None,
                     "warn": warn})
    return {"company": c["company"], "vendor_id": int(vendor_id), "rule": rule, "rule_pct": pct,
            "rule_label": RULE_LABEL.get(rule or ""), "rows": rows, "missing_price": missing,
            "note": None if rule else "Set a price rule for this company first (or a rule on the article)."}


def apply_prices(conn: sqlite3.Connection, ws: str, vendor_id: int, ids: list[int], *, user: str = "") -> dict[str, Any]:
    """Save the rule price on the articles the user ticked (price history keeps the old price)."""
    from app import hop_dist_extra as X

    chk = {int(r["id"]): r for r in price_check(conn, ws, vendor_id)["rows"]}
    done = 0
    for i in ids or []:
        r = chk.get(int(i))
        if not r or r["current"] == r["suggested"]:
            continue
        X.save_article(conn, ws, {"id": int(i), "selling_price": r["suggested"]}, user=user, source="price_rule")
        done += 1
    return {"updated": done, **overview(conn, ws)}


def assign_company(conn: sqlite3.Connection, ws: str, ids: list[int], vendor_id: int | None) -> dict[str, Any]:
    """Put the ticked articles under one company (None clears it)."""
    ensure_schema(conn)
    if vendor_id and not conn.execute("SELECT 1 FROM hop_vendors WHERE workspace_id=? AND id=? AND is_principal=1",
                                      (ws, int(vendor_id))).fetchone():
        raise LookupError("Company not found")
    n = 0
    for i in ids or []:
        n += conn.execute("UPDATE hop_products SET vendor_id=? WHERE workspace_id=? AND id=?",
                          (int(vendor_id) if vendor_id else None, ws, int(i))).rowcount
    conn.commit()
    return {"updated": n}


def summary_text(conn: sqlite3.Connection, ws: str) -> str:
    """For Ask Nexora / AI insights: one line per company."""
    o = overview(conn, ws)
    lines = []
    for c in o["companies"]:
        bits = [c["company"]]
        if c.get("payment_terms"):
            bits.append(f"terms {c['payment_terms']}")
        if c.get("credit_days") is not None:
            bits.append(f"{c['credit_days']} days credit")
        if c.get("cash_discount_pct"):
            bits.append(f"CD {c['cash_discount_pct']:g}%")
        if c.get("rule"):
            bits.append(f"{c['rule_label']} {c['rule_pct']:g}%")
        bits.append(f"{c['articles']} articles, {c['off_rule']} not at the rule price")
        lines.append(" · ".join(bits))
    if o["unassigned"]:
        lines.append(f"{o['unassigned']} articles have no company yet")
    return "\n".join(lines) or "No company set yet."

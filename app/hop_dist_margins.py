"""Distributor: Terms book — the margin chain of every article, per company (owner 21-11).

A company's price sheet works as a chain (checked on real company booking sheets, numbers not kept here):

    selling  = MRP × (1 − perceived %)                    consumer price after the "perceived" discount (often 0)
    base     = selling ÷ (1 + GST)   when GST comes out before the retailer margin ("retail")
    PTR      = base × (1 − retailer %)                    price to the retailer (retailer margin / mark-down)
    PTR ex   = PTR ÷ (1 + GST)       when GST comes out before the distributor mark-up ("awd")
    ex-mill  = PTR ex ÷ (1 + distributor %)               my buying price from the company (DP)
    my margin = PTR (ex GST) − ex-mill

The retailer % changes product to product, the distributor % is about the same for a category — so:
  * every article can carry its own chain (``mg_from='own'``, typed or read from the company's sheet);
  * otherwise it follows the **default of its company + category** (read live, so a new default reaches every
    article that follows it; an article with its own chain never changes by itself).
Nothing changes a price by itself: ``apply`` saves PTR / ex-mill only on the articles the user ticked.
"""

from __future__ import annotations

import sqlite3
from typing import Any

FIELDS = ("perceived_pct", "retail_pct", "gst_out", "awd_pct", "season")
GST_OUT = ("retail", "awd", "none")
GST_OUT_LABEL = {"retail": "GST out before the retailer margin", "awd": "GST out before my mark-up",
                 "none": "No GST step"}
_COL = {"perceived_pct": "mg_perceived_pct", "retail_pct": "mg_retail_pct", "gst_out": "mg_gst_out",
        "awd_pct": "mg_awd_pct", "season": "mg_season"}


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _s(v: Any) -> str:
    return str(v or "").strip()


def ensure_schema(conn: sqlite3.Connection) -> None:
    from app import hop_dist_terms as T

    T.ensure_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hop_products)")}
    for col, typ in (("mg_perceived_pct", "REAL"), ("mg_retail_pct", "REAL"), ("mg_gst_out", "TEXT"),
                     ("mg_awd_pct", "REAL"), ("mg_season", "TEXT"), ("mg_from", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE hop_products ADD COLUMN {col} {typ}")
    vcols = {r[1] for r in conn.execute("PRAGMA table_info(hop_vendors)")}
    if "contact_designation" not in vcols:   # contact person's designation at the company (owner 21-11)
        conn.execute("ALTER TABLE hop_vendors ADD COLUMN contact_designation TEXT")
    if "pincode" not in vcols:
        conn.execute("ALTER TABLE hop_vendors ADD COLUMN pincode TEXT")
    if "contacts_json" not in vcols:   # more contact persons: [{name, designation, mobile, email}] (owner 21-11)
        conn.execute("ALTER TABLE hop_vendors ADD COLUMN contacts_json TEXT")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_dist_margin_defaults (
               workspace_id TEXT NOT NULL,
               vendor_id INTEGER NOT NULL,
               category TEXT NOT NULL,
               perceived_pct REAL, retail_pct REAL, gst_out TEXT, awd_pct REAL, season TEXT,
               updated_at TEXT,
               PRIMARY KEY (workspace_id, vendor_id, category)
           )""")


def chain(mrp: float, gst_pct: float, m: dict) -> dict[str, float] | None:
    """The prices the chain gives for one article; None when MRP or the retailer % is missing."""
    mrp = _f(mrp)
    if mrp <= 0 or m.get("retail_pct") in (None, ""):
        return None
    g = 1 + (_f(gst_pct) if gst_pct not in (None, "") else 5.0) / 100
    out = _s(m.get("gst_out")) or "none"
    selling = mrp * (1 - _f(m.get("perceived_pct")) / 100)
    base = selling / g if out == "retail" else selling
    ptr = base * (1 - _f(m.get("retail_pct")) / 100)
    ptr_ex = ptr / g if out == "awd" else ptr
    res = {"selling": round(selling, 2), "ptr": round(ptr, 2)}
    if m.get("awd_pct") not in (None, ""):
        ex = ptr_ex / (1 + _f(m.get("awd_pct")) / 100)
        res["ex_mill"] = round(ex, 2)
        res["my_margin"] = round(ptr_ex - ex, 2)
        res["my_margin_pct"] = round((ptr_ex - ex) / ptr_ex * 100, 1) if ptr_ex else None
    return res


def _clean(p: dict) -> dict[str, Any]:
    """Validated chain fields from a form; only the keys sent."""
    out: dict[str, Any] = {}
    for k, lo, hi in (("perceived_pct", 0, 90), ("retail_pct", 0, 90), ("awd_pct", 0, 200)):
        if k in p:
            v = p.get(k)
            if v in (None, ""):
                out[k] = None
                continue
            try:
                x = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k.replace('_pct', '').replace('_', ' ')} %: type a number")
            if not lo <= x <= hi:
                raise ValueError(f"{k.replace('_pct', '').replace('_', ' ')} % must be between {lo} and {hi}")
            out[k] = x
    if "gst_out" in p:
        g = _s(p.get("gst_out")) or None
        if g and g not in GST_OUT:
            raise ValueError("GST step must be retail, awd or none")
        out["gst_out"] = g
    if "season" in p:
        out["season"] = _s(p.get("season"))[:20] or None
    return out


def _company(conn, ws, vid: int) -> dict:
    r = conn.execute("SELECT id, company FROM hop_vendors WHERE workspace_id=? AND id=? AND is_principal=1",
                     (ws, int(vid))).fetchone()
    if not r:
        raise LookupError("Company not found — add it in My brands or tick it in Orders → SO → CI → Set company")
    return {"id": int(r[0]), "company": r[1]}


def _defaults(conn, ws, vid: int) -> dict[str, dict]:
    return {_s(r["category"]): dict(r) for r in conn.execute(
        "SELECT * FROM hop_dist_margin_defaults WHERE workspace_id=? AND vendor_id=?", (ws, int(vid)))}


def _company_articles(conn, ws, vid: int) -> list[dict]:
    from app import hop_dist_terms as T

    ids = {int(c["id"]) for c in T._companies(conn, ws)}
    only = next(iter(ids)) if len(ids) == 1 else None
    rows = [dict(r) for r in conn.execute(
        "SELECT id, code, name, category, mrp, purchase_price, selling_price, gst_pct, vendor_id, mg_perceived_pct, "
        "mg_retail_pct, mg_gst_out, mg_awd_pct, mg_season, mg_from FROM hop_products WHERE workspace_id=? "
        "AND COALESCE(status,'active')='active' AND (deleted_at IS NULL OR trim(deleted_at)='') ORDER BY lower(name)",
        (ws,)).fetchall()]
    return [a for a in rows if T._owner(a, ids, only) == int(vid)]


def _effective(a: dict, defs: dict[str, dict]) -> tuple[dict, str]:
    if _s(a.get("mg_from")) == "own":
        return {k: a.get(_COL[k]) for k in FIELDS}, "own"
    d = defs.get(_s(a.get("category")))
    if d:
        return {k: d.get(k) for k in FIELDS}, "default"
    return {}, "none"


def book(conn: sqlite3.Connection, ws: str, vendor_id: int) -> dict[str, Any]:
    """One company: the default chain of each category, and every article with its chain, prices and checks."""
    ensure_schema(conn)
    c = _company(conn, ws, vendor_id)
    defs = _defaults(conn, ws, vendor_id)
    arts = _company_articles(conn, ws, vendor_id)
    cats: dict[str, dict] = {}
    rows = []
    for a in arts:
        cat = _s(a.get("category")) or "No category"
        m, src = _effective(a, defs)
        p = chain(a.get("mrp"), a.get("gst_pct"), m) if m else None
        checks = []
        if p and p.get("ex_mill") and _f(a.get("purchase_price")) and abs(p["ex_mill"] - _f(a["purchase_price"])) >= 1:
            checks.append(f"Ex-mill by the terms ₹{p['ex_mill']:,.2f}, DP saved ₹{_f(a['purchase_price']):,.2f}")
        if p and _f(a.get("selling_price")) and abs(p["ptr"] - _f(a["selling_price"])) >= 1:
            checks.append(f"PTR by the terms ₹{p['ptr']:,.2f}, price to retailer saved ₹{_f(a['selling_price']):,.2f}")
        if not _f(a.get("mrp")):
            checks.append("No MRP")
        k = cats.setdefault(cat, {"category": cat, "articles": 0, "own": 0, "no_terms": 0, "checks": 0})
        k["articles"] += 1
        k["own"] += src == "own"
        k["no_terms"] += src == "none"
        k["checks"] += bool(checks)
        rows.append({"id": a["id"], "code": a.get("code"), "name": a["name"], "category": cat,
                     "mrp": _f(a.get("mrp")) or None, "dp": _f(a.get("purchase_price")) or None,
                     "ptr_saved": _f(a.get("selling_price")) or None, "gst_pct": a.get("gst_pct"),
                     "terms": {k2: m.get(k2) for k2 in FIELDS} if m else None, "from": src,
                     "prices": p, "checks": checks})
    for cat, k in cats.items():
        d = defs.get(cat)
        k["default"] = {f: d.get(f) for f in FIELDS} if d else None
    return {"company": c["company"], "vendor_id": c["id"], "categories": sorted(cats.values(), key=lambda x: x["category"].lower()),
            "articles": rows, "gst_out": [{"key": g, "label": GST_OUT_LABEL[g]} for g in GST_OUT],
            "summary": {"articles": len(rows), "own": sum(r["from"] == "own" for r in rows),
                        "no_terms": sum(r["from"] == "none" for r in rows), "checks": sum(bool(r["checks"]) for r in rows)}}


def save_default(conn: sqlite3.Connection, ws: str, vendor_id: int, category: str, p: dict) -> dict[str, Any]:
    """Default chain for a company + category. Articles that follow it use it at once; own ones never change."""
    from app.hop_dist_extra import _now

    ensure_schema(conn)
    _company(conn, ws, vendor_id)
    cat = _s(category)[:80]
    if not cat:
        raise ValueError("Category is required")
    vals = _clean(p)
    if p.get("clear"):
        conn.execute("DELETE FROM hop_dist_margin_defaults WHERE workspace_id=? AND vendor_id=? AND category=?",
                     (ws, int(vendor_id), cat))
    else:
        if vals.get("retail_pct") is None and "retail_pct" in vals:
            raise ValueError("Retailer % is required")
        old = _defaults(conn, ws, vendor_id).get(cat) or {}
        row = {f: (vals[f] if f in vals else old.get(f)) for f in FIELDS}
        if row.get("retail_pct") is None:
            raise ValueError("Retailer % is required")
        conn.execute(
            """INSERT INTO hop_dist_margin_defaults (workspace_id, vendor_id, category, perceived_pct, retail_pct, gst_out,
                   awd_pct, season, updated_at) VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(workspace_id, vendor_id, category) DO UPDATE SET perceived_pct=excluded.perceived_pct,
                   retail_pct=excluded.retail_pct, gst_out=excluded.gst_out, awd_pct=excluded.awd_pct,
                   season=excluded.season, updated_at=excluded.updated_at""",
            (ws, int(vendor_id), cat, row["perceived_pct"], row["retail_pct"], row["gst_out"] or "none",
             row["awd_pct"], row["season"], _now()))
    conn.commit()
    return book(conn, ws, vendor_id)


def following(conn: sqlite3.Connection, ws: str, vendor_id: int, category: str) -> int:
    """How many articles of this company + category follow the default (asked before the default changes)."""
    ensure_schema(conn)
    return sum(1 for a in _company_articles(conn, ws, vendor_id)
               if (_s(a.get("category")) or "No category") == _s(category) and _s(a.get("mg_from")) != "own")


def save_article(conn: sqlite3.Connection, ws: str, product_id: int, p: dict) -> dict[str, Any]:
    """An article's own chain (typed, or read from the company sheet). {"follow_default": true} goes back."""
    from app.hop_dist_extra import _now

    ensure_schema(conn)
    r = conn.execute("SELECT id FROM hop_products WHERE workspace_id=? AND id=?", (ws, int(product_id))).fetchone()
    if not r:
        raise LookupError("Article not found")
    if p.get("follow_default"):
        conn.execute("UPDATE hop_products SET mg_from=NULL, updated_at=? WHERE workspace_id=? AND id=?",
                     (_now(), ws, int(product_id)))
    else:
        vals = _clean(p)
        if not vals:
            raise ValueError("Nothing to save")
        sets = [f"{_COL[k]}=?" for k in vals] + ["mg_from='own'", "updated_at=?"]
        conn.execute(f"UPDATE hop_products SET {', '.join(sets)} WHERE workspace_id=? AND id=?",
                     [*vals.values(), _now(), ws, int(product_id)])
    conn.commit()
    return {"id": int(product_id)}


def apply(conn: sqlite3.Connection, ws: str, vendor_id: int, ids: list[int], *, user: str = "") -> dict[str, Any]:
    """Save PTR (price to retailer) and ex-mill (DP) from the terms on the ticked articles; price history keeps the old."""
    from app import hop_dist_extra as X

    rows = {int(r["id"]): r for r in book(conn, ws, vendor_id)["articles"]}
    done = 0
    for i in ids or []:
        r = rows.get(int(i))
        if not r or not r.get("prices"):
            continue
        ch: dict[str, Any] = {"id": int(i)}
        if r["ptr_saved"] is None or abs(r["prices"]["ptr"] - r["ptr_saved"]) >= 1:
            ch["selling_price"] = r["prices"]["ptr"]
        ex = r["prices"].get("ex_mill")
        if ex and (r["dp"] is None or abs(ex - r["dp"]) >= 1):
            ch["purchase_price"] = ex
        if len(ch) > 1:
            X.save_article(conn, ws, ch, user=user, source="terms_book")
            done += 1
    return {"updated": done, **book(conn, ws, vendor_id)}


def summary_text(conn: sqlite3.Connection, ws: str) -> str:
    """For Ask Nexora / AI insights: one line per company — categories with terms, articles without, checks."""
    from app import hop_dist_terms as T

    ensure_schema(conn)
    lines = []
    for c in T._companies(conn, ws):
        b = book(conn, ws, int(c["id"]))
        cats = [k for k in b["categories"] if k.get("default")]
        s = b["summary"]
        lines.append(f"{b['company']}: {len(cats)} categories with default terms, {s['own']} articles with their own "
                     f"terms, {s['no_terms']} without terms, {s['checks']} where DP / PTR do not match the terms")
    return "\n".join(lines) or "No company set yet."


def add_company(conn: sqlite3.Connection, ws: str, p: dict) -> dict[str, Any]:
    """A company the distributor buys from (e.g. a brand like company B or C) with its own details and GSTIN.
    Saved as a supplier and ticked as a company in one go (Brand desk → + Add company)."""
    import re

    from app import hop_distributor as D
    from app.hop_ops import create_vendor

    ensure_schema(conn)
    name = _s(p.get("company"))[:120]
    if not name:
        raise ValueError("Company name is required")
    gst = _s(p.get("gst_no")).upper()
    if gst and not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]", gst):
        raise ValueError("GSTIN looks wrong — 15 characters like 27ABCDE1234F1Z5")
    dup = conn.execute("SELECT id, company FROM hop_vendors WHERE workspace_id=? AND (lower(company)=lower(?) OR "
                       "(?<>'' AND upper(COALESCE(gst_no,''))=?))", (ws, name, gst, gst)).fetchone()
    if dup:
        D.set_principal(conn, ws, int(dup[0]), True)
        return {"id": int(dup[0]), "company": dup[1], "existed": True}
    v = create_vendor(conn, ws, {k: p.get(k) for k in ("company", "gst_no", "contact_person", "mobile", "email",
                                                       "address", "state", "city", "payment_terms")} |
                      {"company": name, "gst_no": gst})
    D.set_principal(conn, ws, int(v["id"]), True)
    from app import hop_dist_company as CO

    extra = {k: p[k] for k in ("contact_designation", "pincode", "contacts") if k in p}
    if extra:
        CO.edit_company(conn, ws, int(v["id"]), extra)
    return {"id": int(v["id"]), "company": name, "existed": False}


def need_company(conn: sqlite3.Connection, ws: str, p: dict) -> str | None:
    """A new article must say which company it is from. Returns the message to show, or None when it is fine."""
    from app import hop_dist_terms as T

    if p.get("id") or p.get("vendor_id"):
        return None
    comps = T._companies(conn, ws)
    if not comps:
        return ("Which company is this article from? No company is added yet — add the company (name, GSTIN, "
                "address) first, then save the article again.")
    if len(comps) == 1:
        return None   # the only company is taken
    return "Which company is this article from? Pick the company, then save again."


# ══ ✨ Terms from the company's sheet ═════════════════════════════════════════════════════════════════════
# A company booking sheet read in Article Master keeps its % columns as article details ("Retailer Margin 28%",
# "AWD Mark up on Exmill 15%", "Perceived 40%", "Retail Mark down 30%", "AWD MD 18%", "AWD MU 17%", "Retailer MD 33%").
# Here they become each article's own terms; the GST step is found by matching the saved MRP / PTR / DP.
_RETAIL_KEYS = ("retailer margin", "retail margin", "retail mark down", "retail markdown", "retailer md", "retail md",
                "retailer mark down", "rmd")
_AWD_KEYS = ("awd mark up", "awd markup", "awd mu", "awd md", "mark up on exmill", "markup on exmill", "distributor margin",
             "awd margin")
_PERCEIVED_KEYS = ("perceived", "consumer discount", "consumer offer")


def _pct_in(details: dict, keys: tuple[str, ...]) -> float | None:
    import re

    for k, v in (details or {}).items():
        h = re.sub(r"[^a-z ]", " ", str(k).lower())
        h = re.sub(r"\s+", " ", h).strip()
        if any(w in h for w in keys):
            m = re.search(r"-?\d+(\.\d+)?", str(v))
            if m:
                x = float(m.group(0))
                return round(x * 100, 2) if 0 < x < 1 else x
    return None


def suggest_from_sheet(conn: sqlite3.Connection, ws: str, vendor_id: int) -> dict[str, Any]:
    """✨ Every article of the company whose sheet details carry its margins → the terms they give, with a check
    against the saved prices. Nothing is saved here."""
    import json

    ensure_schema(conn)
    c = _company(conn, ws, vendor_id)
    ids = {int(a["id"]) for a in _company_articles(conn, ws, vendor_id)}
    rows = []
    for r in conn.execute("SELECT id, name, category, mrp, purchase_price, selling_price, gst_pct, custom_fields, mg_from, season "
                          "FROM hop_products WHERE workspace_id=?", (ws,)):
        if int(r["id"]) not in ids:
            continue
        try:
            det = json.loads(r["custom_fields"] or "{}")
        except Exception:
            det = {}
        retail = _pct_in(det, _RETAIL_KEYS)
        if retail is None:
            continue
        awd, perceived = _pct_in(det, _AWD_KEYS), _pct_in(det, _PERCEIVED_KEYS)
        mrp, ptr_saved, dp = _f(r["mrp"]), _f(r["selling_price"]), _f(r["purchase_price"])
        best, best_err = None, None
        for g in GST_OUT:   # the GST step that matches the saved prices best
            p = chain(mrp, r["gst_pct"], {"perceived_pct": perceived, "retail_pct": retail, "gst_out": g, "awd_pct": awd})
            if not p:
                continue
            err = (abs(p["ptr"] - ptr_saved) if ptr_saved else 0) + (abs(p.get("ex_mill", 0) - dp) if dp and "ex_mill" in p else 0)
            if best_err is None or err < best_err:
                best, best_err = g, err
        if best is None:
            continue
        terms = {"perceived_pct": perceived, "retail_pct": retail, "gst_out": best, "awd_pct": awd, "season": r["season"]}
        p = chain(mrp, r["gst_pct"], terms)
        rows.append({"id": int(r["id"]), "name": r["name"], "category": r["category"], "terms": terms, "prices": p,
                     "matches_saved": (best_err or 0) < 1.0 if (ptr_saved or dp) else None,
                     "has_own": _s(r["mg_from"]) == "own"})
    return {"company": c["company"], "vendor_id": c["id"], "rows": rows,
            "note": None if rows else "No margin columns found in this company's article details — read its booking "
                                      "sheet in Article Master first (the % columns are kept as details)."}


def apply_from_sheet(conn: sqlite3.Connection, ws: str, vendor_id: int, ids: list[int]) -> dict[str, Any]:
    """Save the sheet's terms as the own terms of the ticked articles."""
    sug = {r["id"]: r for r in suggest_from_sheet(conn, ws, vendor_id)["rows"]}
    n = 0
    for i in ids or []:
        r = sug.get(int(i))
        if r:
            save_article(conn, ws, int(i), {k: v for k, v in r["terms"].items()})
            n += 1
    return {"saved": n, **book(conn, ws, vendor_id)}

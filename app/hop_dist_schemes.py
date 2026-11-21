"""Company schemes with dates (owner 21-11: "CD or incentives bhi time to time badalte rehte hai kaise monitor karoge").

Two kinds, each with its own from / to dates, so a change never rewrites the past:

* ``cd`` — cash discount slabs: pay within N days of the invoice → X % (e.g. 7 days 3 %, 15 days 2 %). Every company
  invoice uses the CD scheme that was running on ITS invoice date. No scheme on that date → the company's old single
  CD % / credit days (kept from before, shown as "Old CD % (no dates)").
* ``incentive`` — target slabs on purchases in the period (e.g. ₹10 lakh → 1 %, ₹15 lakh → 1.5 %), on all goods or
  some categories. Progress is counted by itself from the company invoices (CI) in the period; the credit note the
  company sends is linked when it comes.

``alerts`` lists what needs attention: invoices to pay soon to keep the CD, a target close to the next slab, a scheme
ending, an incentive earned but its credit note not received. Nothing here changes a bill or a price.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

KINDS = ("cd", "incentive")
BASES = ("all", "category")


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _s(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def _now() -> str:
    from app.hop_dist_extra import _now as n

    return n()


def _today() -> date:
    from app.hop_dist_extra import _today as t

    return t()


def _d(v: Any) -> date | None:
    try:
        return date.fromisoformat(_s(v)[:10])
    except ValueError:
        return None


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_dist_schemes (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               workspace_id TEXT NOT NULL,
               vendor_id INTEGER NOT NULL,
               kind TEXT NOT NULL,
               title TEXT,
               from_date TEXT NOT NULL,
               to_date TEXT,
               basis TEXT DEFAULT 'all',
               categories TEXT,
               slabs TEXT NOT NULL,
               note TEXT,
               cn_txn_id INTEGER,
               received_amount REAL,
               source TEXT,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_hop_dist_schemes ON hop_dist_schemes(workspace_id, vendor_id, kind)")


def _company(conn, ws, vid: int) -> dict:
    r = conn.execute("SELECT id, company, cash_discount_pct, credit_days FROM hop_vendors WHERE workspace_id=? AND id=?",
                     (ws, int(vid))).fetchone()
    if not r:
        raise LookupError("Company not found")
    return dict(r)


def _row(r: sqlite3.Row | dict) -> dict[str, Any]:
    d = dict(r)
    for k in ("slabs", "categories"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except ValueError:
            d[k] = []
    return d


# ══ save / list / delete ══════════════════════════════════════════════════════════════════════════════════
def _clean_slabs(kind: str, slabs: Any) -> list[dict[str, float]]:
    out = []
    for s in slabs or []:
        if not isinstance(s, dict):
            continue
        pct = _f(s.get("pct"))
        if kind == "cd":
            days = int(_f(s.get("days")))
            if days <= 0 or not 0 < pct <= 20:
                raise ValueError("CD slab: days above 0 and % between 0 and 20")
            out.append({"days": days, "pct": pct})
        else:
            target = _f(s.get("target"))
            amount = _f(s.get("amount"))
            if target <= 0 or (pct <= 0 and amount <= 0):
                raise ValueError("Incentive slab: a target above 0 and a % or an amount")
            if pct > 50:
                raise ValueError("Incentive % must be 50 or less")
            out.append({"target": target, "pct": pct, "amount": amount})
    if not out:
        raise ValueError("Add at least one slab")
    key = "days" if kind == "cd" else "target"
    out.sort(key=lambda s: s[key])
    if len({s[key] for s in out}) != len(out):
        raise ValueError("Two slabs have the same " + ("days" if kind == "cd" else "target"))
    return out


def save(conn: sqlite3.Connection, ws: str, vid: int, p: dict) -> dict[str, Any]:
    """Create or change one scheme: {id?, kind, title, from_date, to_date?, basis, categories, slabs, note,
    cn_txn_id?, received_amount?}."""
    ensure_schema(conn)
    _company(conn, ws, vid)
    kind = _s(p.get("kind")).lower()
    if kind not in KINDS:
        raise ValueError("Kind must be cd or incentive")
    frm, to = _d(p.get("from_date")), _d(p.get("to_date")) if _s(p.get("to_date")) else None
    if not frm:
        raise ValueError("From date is required")
    if to and to < frm:
        raise ValueError("To date is before the from date")
    if kind == "incentive" and not to:
        raise ValueError("An incentive needs its last date (to date)")
    basis = _s(p.get("basis")) or "all"
    if basis not in BASES:
        raise ValueError("Basis must be all or category")
    cats = [c for c in (_s(x)[:60] for x in (p.get("categories") or [])) if c][:20]
    if basis == "category" and not cats:
        raise ValueError("Pick the categories this scheme is on")
    slabs = _clean_slabs(kind, p.get("slabs"))
    title = _s(p.get("title"))[:120] or ("Cash discount" if kind == "cd" else "Incentive")
    vals = dict(kind=kind, title=title, from_date=frm.isoformat(), to_date=to.isoformat() if to else None, basis=basis,
                categories=json.dumps(cats, ensure_ascii=False), slabs=json.dumps(slabs), note=_s(p.get("note"))[:500] or None,
                cn_txn_id=int(p["cn_txn_id"]) if _s(p.get("cn_txn_id")) else None,
                received_amount=_f(p.get("received_amount")) or None, source=_s(p.get("source"))[:20] or "typed")
    if p.get("id"):
        sid = int(p["id"])
        if not conn.execute("SELECT 1 FROM hop_dist_schemes WHERE workspace_id=? AND vendor_id=? AND id=?",
                            (ws, int(vid), sid)).fetchone():
            raise LookupError("Scheme not found")
        conn.execute(f"UPDATE hop_dist_schemes SET {', '.join(k + '=?' for k in vals)}, updated_at=? WHERE id=?",
                     [*vals.values(), _now(), sid])
    else:
        cur = conn.execute(
            f"INSERT INTO hop_dist_schemes (workspace_id, vendor_id, {', '.join(vals)}, created_at, updated_at) "
            f"VALUES (?, ?, {', '.join('?' * len(vals))}, ?, ?)", [ws, int(vid), *vals.values(), _now(), _now()])
        sid = int(cur.lastrowid)
    conn.commit()
    return _row(conn.execute("SELECT * FROM hop_dist_schemes WHERE id=?", (sid,)).fetchone())


def delete(conn: sqlite3.Connection, ws: str, sid: int) -> dict[str, Any]:
    ensure_schema(conn)
    n = conn.execute("DELETE FROM hop_dist_schemes WHERE workspace_id=? AND id=?", (ws, int(sid))).rowcount
    if not n:
        raise LookupError("Scheme not found")
    conn.commit()
    return {"deleted": int(sid)}


def _schemes(conn, ws, vid: int, kind: str | None = None) -> list[dict]:
    ensure_schema(conn)
    sql = "SELECT * FROM hop_dist_schemes WHERE workspace_id=? AND vendor_id=?"
    args: list[Any] = [ws, int(vid)]
    if kind:
        sql += " AND kind=?"; args.append(kind)
    return [_row(r) for r in conn.execute(sql + " ORDER BY from_date DESC, id DESC", args)]


# ══ cash discount per invoice ═════════════════════════════════════════════════════════════════════════════
def cd_for(conn, ws, vid: int, on: date, legacy: dict | None = None) -> dict | None:
    """The CD rule running on an invoice date: the newest CD scheme whose dates cover it, else the old single CD %."""
    for s in _schemes(conn, ws, vid, "cd"):
        f, t = _d(s["from_date"]), _d(s["to_date"]) if s.get("to_date") else None
        if f and f <= on and (t is None or on <= t):
            return {"id": s["id"], "title": s["title"], "slabs": s["slabs"], "dated": True}
    c = legacy if legacy is not None else _company(conn, ws, vid)
    if _f(c.get("cash_discount_pct")) > 0:
        return {"id": None, "title": "Old CD % (no dates)", "dated": False,
                "slabs": [{"days": int(c.get("credit_days") or 0), "pct": _f(c.get("cash_discount_pct"))}]}
    return None


def cd_status(rule: dict, amount: float, inv: date, paid_on: date | None, balance: float, today: date) -> dict[str, Any]:
    """One invoice under one CD rule: the slab earned (paid) or still open (unpaid), the date to pay by, the amount."""
    slabs = sorted(rule["slabs"], key=lambda s: s["days"])
    if balance <= 0.005:
        days = ((paid_on or today) - inv).days
        hit = next((s for s in slabs if days <= s["days"]), None)
        if hit:
            return {"status": "earned", "pct": hit["pct"], "cd": round(amount * hit["pct"] / 100, 2),
                    "pay_by": (inv + timedelta(days=hit["days"])).isoformat()}
        best = slabs[0]
        return {"status": "lost", "pct": best["pct"], "cd": round(amount * best["pct"] / 100, 2),
                "pay_by": (inv + timedelta(days=slabs[-1]["days"])).isoformat()}
    days = (today - inv).days
    nxt = next((s for s in slabs if days <= s["days"]), None)
    if nxt:
        return {"status": "open", "pct": nxt["pct"], "cd": round(amount * nxt["pct"] / 100, 2),
                "pay_by": (inv + timedelta(days=nxt["days"])).isoformat()}
    best = slabs[0]
    return {"status": "lost", "pct": best["pct"], "cd": round(amount * best["pct"] / 100, 2),
            "pay_by": (inv + timedelta(days=slabs[-1]["days"])).isoformat()}


# ══ incentive progress ════════════════════════════════════════════════════════════════════════════════════
def _bought(conn, ws, vid: int, frm: str, to: str, cats: list[str]) -> float:
    """Purchases from the company in the period (invoice totals; on categories: the taxable lines of those)."""
    from app.hop_dist_company import LIVE

    if not cats:
        r = conn.execute(f"""SELECT COALESCE(SUM(t.total_amount),0) FROM hop_party_transactions t
                             WHERE t.workspace_id=? AND t.txn_type=2 AND t.party_type='vendor' AND t.party_id=?
                             AND date(t.txn_date) BETWEEN date(?) AND date(?) AND {LIVE}""", (ws, int(vid), frm, to)).fetchone()
        return round(_f(r[0]), 2)
    ph = ",".join("?" * len(cats))
    r = conn.execute(f"""SELECT COALESCE(SUM(ABS(COALESCE(l.qty,0)) * COALESCE(l.rate,0)
                                - COALESCE(l.discount_amount,0)),0)
                         FROM hop_party_transactions t JOIN hop_txn_lines l
                           ON l.workspace_id=t.workspace_id AND l.source_txn_id=t.id
                         JOIN hop_products p ON p.workspace_id=t.workspace_id AND p.id=l.product_id
                         WHERE t.workspace_id=? AND t.txn_type=2 AND t.party_type='vendor' AND t.party_id=?
                           AND date(t.txn_date) BETWEEN date(?) AND date(?) AND {LIVE}
                           AND lower(COALESCE(p.category,'')) IN ({ph})""",
                     (ws, int(vid), frm, to, *[c.lower() for c in cats])).fetchone()
    return round(_f(r[0]), 2)


def progress(conn, ws, s: dict, today: date | None = None) -> dict[str, Any]:
    """One incentive scheme: bought so far, slab reached, next slab and the gap, days left, expected payout."""
    today = today or _today()
    f, t = _d(s["from_date"]), _d(s["to_date"])
    upto = min(t, today) if t else today
    bought = _bought(conn, ws, int(s["vendor_id"]), f.isoformat(), upto.isoformat(),
                     s["categories"] if s.get("basis") == "category" else [])
    slabs = sorted(s["slabs"], key=lambda x: x["target"])
    reached = None
    for sl in slabs:
        if bought >= sl["target"]:
            reached = sl
    nxt = next((sl for sl in slabs if bought < sl["target"]), None)

    def pay(sl):
        return round(sl["amount"] if sl.get("amount") else bought * sl["pct"] / 100, 2) if sl else 0.0

    ended = bool(t and today > t)
    status = "running" if not ended else ("earned" if reached else "missed")
    if status == "earned" and s.get("cn_txn_id"):
        status = "received"
    return {"id": s["id"], "title": s["title"], "from_date": s["from_date"], "to_date": s["to_date"],
            "basis": s.get("basis"), "categories": s.get("categories"), "slabs": slabs, "bought": bought,
            "reached": reached, "earned_now": pay(reached), "next": nxt,
            "gap": round(nxt["target"] - bought, 2) if nxt else 0.0, "next_payout": pay(nxt) if nxt else None,
            "days_left": (t - today).days if t and not ended else 0, "status": status,
            "cn_txn_id": s.get("cn_txn_id"), "received_amount": s.get("received_amount"),
            "short_received": round(pay(reached) - _f(s.get("received_amount")), 2)
            if status == "received" and s.get("received_amount") is not None else None}


# ══ one company's schemes + alerts ════════════════════════════════════════════════════════════════════════
def company_schemes(conn: sqlite3.Connection, ws: str, vid: int) -> dict[str, Any]:
    """CD schemes (newest first, the old single CD % at the end) and incentives with their progress."""
    ensure_schema(conn)
    c = _company(conn, ws, vid)
    today = _today()
    cds = []
    for s in _schemes(conn, ws, vid, "cd"):
        f, t = _d(s["from_date"]), _d(s["to_date"]) if s.get("to_date") else None
        s["running"] = bool(f and f <= today and (t is None or today <= t))
        cds.append(s)
    legacy = None
    if _f(c.get("cash_discount_pct")) > 0:
        legacy = {"title": "Old CD % (no dates)", "slabs": [{"days": int(c.get("credit_days") or 0),
                                                              "pct": _f(c.get("cash_discount_pct"))}],
                  "note": "Used for invoices no dated CD scheme covers."}
    inc = [progress(conn, ws, s, today) for s in _schemes(conn, ws, vid, "incentive")]
    return {"company": c["company"], "vendor_id": c["id"], "cd": cds, "cd_old": legacy, "incentives": inc,
            "alerts": alerts(conn, ws, vid)}


def alerts(conn: sqlite3.Connection, ws: str, vid: int | None = None) -> list[dict[str, Any]]:
    """What needs attention, most urgent first: CD to keep (pay in ≤ 7 days), near the next target, scheme ending,
    incentive earned without its credit note."""
    from app import hop_dist_company as CO

    ensure_schema(conn)
    vids = [int(vid)] if vid else [int(r[0]) for r in conn.execute(
        "SELECT id FROM hop_vendors WHERE workspace_id=? AND is_principal=1", (ws,))]
    out: list[dict[str, Any]] = []
    today = _today()
    for v in vids:
        try:
            name = _company(conn, ws, v)["company"]
            cd = CO.cash_discount(conn, ws, v)
        except LookupError:
            continue
        for r in cd.get("pay_soon") or []:
            out.append({"kind": "cd", "vendor_id": v, "company": name, "rank": 0,
                        "text": f"{name}: pay {r['ci_no']} (₹{r['balance']:,.0f}) by {r['pay_by']} to keep ₹{r['cd']:,.0f} CD"})
        for s in _schemes(conn, ws, v, "incentive"):
            p = progress(conn, ws, s, today)
            if p["status"] == "running" and p["next"]:
                if p["gap"] <= 0.15 * p["next"]["target"]:
                    out.append({"kind": "target", "vendor_id": v, "company": name, "rank": 1,
                                "text": f"{name} — {p['title']}: ₹{p['gap']:,.0f} more by {p['to_date']} for the next slab "
                                        f"(≈ ₹{p['next_payout']:,.0f})"})
                elif 0 <= p["days_left"] <= 7:
                    out.append({"kind": "ending", "vendor_id": v, "company": name, "rank": 2,
                                "text": f"{name} — {p['title']} ends in {p['days_left']} days; ₹{p['bought']:,.0f} bought, "
                                        f"₹{p['gap']:,.0f} short of the next slab"})
            elif p["status"] == "earned":
                out.append({"kind": "claim", "vendor_id": v, "company": name, "rank": 1,
                            "text": f"{name} — {p['title']} earned ≈ ₹{p['earned_now']:,.0f}; credit note not received yet"})
            elif p["status"] == "received" and (p["short_received"] or 0) >= 1:
                out.append({"kind": "short", "vendor_id": v, "company": name, "rank": 2,
                            "text": f"{name} — {p['title']}: ₹{p['short_received']:,.0f} less received than earned"})
    return sorted(out, key=lambda a: a["rank"])


# ══ ✨ read a scheme letter ════════════════════════════════════════════════════════════════════════════════
_PROMPT = """You read ONE company scheme letter / circular sent to a distributor (cash discount or incentive / target
scheme). Return JSON only:
{"schemes": [{"kind": "cd|incentive", "title": "", "from_date": "YYYY-MM-DD", "to_date": "YYYY-MM-DD or empty",
"categories": [], "slabs": [{"days": 0, "pct": 0}] or [{"target": 0, "pct": 0, "amount": 0}], "note": ""}],
"confidence": "high|medium|low"}
- cd: slabs of "payment within N days → X %". incentive: slabs of "purchase of ₹T → X % (or a fixed ₹ amount)";
  targets in rupees (1 lakh = 100000, 1 crore = 10000000).
- categories: only when the letter limits the scheme to some products (e.g. "Bedsheets", "Towels"); else empty.
- note: conditions in one line (GST excluded, on net billing, etc.).
- Dates exactly as written; leave empty when not written. Never invent a number."""


def read_letter(data: bytes, mime: str, *, user_id: int) -> dict[str, Any]:
    """One capped AI call: scheme letter (photo / PDF) → draft schemes. Nothing is saved — the user checks and saves."""
    from app.services import hop_ai

    out = hop_ai._ask_gemini(data, mime or "image/jpeg", user_id, _PROMPT, max_tokens=700,
                             feature="nexora_agent_hop_scheme_letter")
    drafts = []
    for s in (out.get("schemes") or [])[:6]:
        if not isinstance(s, dict):
            continue
        kind = _s(s.get("kind")).lower()
        if kind not in KINDS:
            continue
        slabs = []
        for x in s.get("slabs") or []:
            if not isinstance(x, dict):
                continue
            if kind == "cd" and _f(x.get("days")) > 0 and _f(x.get("pct")) > 0:
                slabs.append({"days": int(_f(x["days"])), "pct": _f(x["pct"])})
            elif kind == "incentive" and _f(x.get("target")) > 0 and (_f(x.get("pct")) > 0 or _f(x.get("amount")) > 0):
                slabs.append({"target": _f(x["target"]), "pct": _f(x.get("pct")), "amount": _f(x.get("amount"))})
        drafts.append({"kind": kind, "title": _s(s.get("title"))[:120], "from_date": _s(s.get("from_date"))[:10],
                       "to_date": _s(s.get("to_date"))[:10], "basis": "category" if s.get("categories") else "all",
                       "categories": [_s(c)[:60] for c in (s.get("categories") or []) if _s(c)][:20],
                       "slabs": slabs, "note": _s(s.get("note"))[:300], "source": "ai_letter"})
    checks = []
    for d in drafts:
        if not d["from_date"]:
            checks.append(f"{d['title'] or d['kind']}: from date not read — type it.")
        if d["kind"] == "incentive" and not d["to_date"]:
            checks.append(f"{d['title'] or 'Incentive'}: last date not read — type it.")
        if not d["slabs"]:
            checks.append(f"{d['title'] or d['kind']}: slabs not read — type them.")
    if _s(out.get("confidence")) == "low":
        checks.append("Letter was hard to read — check every number.")
    return {"drafts": drafts, "checks": checks, "ai_used": True}

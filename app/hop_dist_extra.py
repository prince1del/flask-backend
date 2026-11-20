"""Distributor UI, part 2 (owner 19-11): Article Master, AI entry checks, retailer payment follow-up, staff
performance, business profitability.

* **Article Master** = the catalogue seen as the company's articles: code, name, category, season, MRP, my buying price
  from the company (DP = purchase_price), my selling price to retailers, margins, stock, and a **price history**
  (every price change is kept). The company's price list (PDF / photo / Excel) is read by AI into a preview — new
  articles, changed prices — and saved only when the user confirms.
* **AI entry checks** (so no wrong data goes in): an SO is checked against my order and the Article Master; a company
  invoice (CI = purchase bill from the principal) against its SO / order and the Article Master; the company's own
  ledger (SOA) against the books.
* **Retailer payment + follow-up**: who owes what and since when, last payment, follow-up log, promises (kept /
  broken), and an AI-style "call today" priority.
* **Staff performance**: per login — bills, sales, collections, follow-ups (from the audit trail and follow-ups).
* **Profitability**: month-wise sales, cost of goods, gross profit, expenses, net; by item, retailer, company.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

LIVE = "(t.deleted_at IS NULL OR trim(t.deleted_at)='')"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _r2(v: float) -> float:
    return round(float(v or 0) + 0.0, 2)


def _s(v: Any) -> str:
    return str(v or "").strip()


def _inr(v: float) -> str:
    """₹1,63,725 (Indian grouping)."""
    n = int(round(abs(float(v or 0))))
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
        s = head + "," + tail
    return ("-" if float(v or 0) < 0 else "") + "₹" + s


def _days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _sim(a: Any, b: Any) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_dist_price_history (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               workspace_id TEXT NOT NULL,
               product_id INTEGER NOT NULL,
               field TEXT NOT NULL,            -- mrp | purchase_price | selling_price
               old_value REAL, new_value REAL,
               season TEXT, source TEXT,       -- manual | price_list | ci
               by_user TEXT, at TEXT NOT NULL
           )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_hop_dist_ph ON hop_dist_price_history(workspace_id, product_id)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_dist_followups (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               workspace_id TEXT NOT NULL,
               party_id INTEGER NOT NULL,
               at TEXT NOT NULL,
               mode TEXT,                      -- call | visit | whatsapp | sms | other
               note TEXT,
               promise_date TEXT, promise_amount REAL,
               by_user TEXT,
               created_at TEXT NOT NULL
           )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_hop_dist_fu ON hop_dist_followups(workspace_id, party_id)")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hop_products)")}
    if "season" not in cols:
        conn.execute("ALTER TABLE hop_products ADD COLUMN season TEXT")
    if "mrp" not in cols:
        conn.execute("ALTER TABLE hop_products ADD COLUMN mrp REAL")
    if "custom_fields" not in cols:   # article details (TC, size, shade, ply, bale pack ...) as JSON
        conn.execute("ALTER TABLE hop_products ADD COLUMN custom_fields TEXT")


# ══ Article Master ════════════════════════════════════════════════════════════════════════════════════════
_ART_COLS = ("id, code, name, category, season, mrp, purchase_price, selling_price, gst_pct, hsn, unit, stock_qty, vendor_id, "
             "updated_at, custom_fields")


def _details(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {str(k)[:40]: str(v)[:80] for k, v in raw.items() if str(v).strip()}
    try:
        d = json.loads(raw) if raw else {}
        return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def _art(r: dict) -> dict[str, Any]:
    dp, sell, mrp = _f(r.get("purchase_price")), _f(r.get("selling_price")), _f(r.get("mrp"))
    r = {**r, "details": _details(r.pop("custom_fields", None))}
    return {**r, "margin": _r2(sell - dp) if sell and dp else None,
            "margin_pct": round((sell - dp) / sell * 100, 1) if sell and dp else None,
            "retailer_margin_pct": round((mrp - sell) / mrp * 100, 1) if mrp and sell else None}


def articles(conn: sqlite3.Connection, ws: str, q: str = "", category: str = "", season: str = "") -> dict[str, Any]:
    ensure_schema(conn)
    sql = f"SELECT {_ART_COLS} FROM hop_products WHERE workspace_id=? AND COALESCE(status,'active')='active' " \
          "AND (deleted_at IS NULL OR trim(deleted_at)='')"
    args: list[Any] = [ws]
    if q:   # name, code, category or any detail ("120 TC", "White", "3-ply")
        sql += (" AND (lower(name) LIKE ? OR lower(COALESCE(code,'')) LIKE ? OR lower(COALESCE(category,'')) LIKE ? "
                "OR lower(COALESCE(custom_fields,'')) LIKE ?)")
        args += [f"%{q.lower()}%"] * 4
    if category:
        sql += " AND lower(COALESCE(category,''))=lower(?)"; args.append(category)
    if season:
        sql += " AND lower(COALESCE(season,''))=lower(?)"; args.append(season)
    rows = [_art(dict(r)) for r in conn.execute(sql + " ORDER BY lower(COALESCE(category,'')), lower(name)", args).fetchall()]
    changes = {int(r[0]): int(r[1]) for r in conn.execute(
        "SELECT product_id, COUNT(*) FROM hop_dist_price_history WHERE workspace_id=? GROUP BY product_id", (ws,))}
    recent = latest_change_by_article(conn, ws)
    for r in rows:
        r["price_changes"] = changes.get(int(r["id"]), 0)
        r["last_change"] = recent.get(int(r["id"]))   # badge on the card: "DP up 8% on 12-11"
    cats = sorted({r["category"] for r in rows if r.get("category")})
    seasons = sorted({r["season"] for r in rows if r.get("season")})
    problems = article_problems(rows)
    pc = price_changes(conn, ws, 30)
    return {"articles": rows, "count": len(rows), "categories": cats, "seasons": seasons, "problems": problems,
            "recent_changes": {"count": pc["count"], "ups": pc["ups"], "downs": pc["downs"],
                               "articles_changed": pc["articles_changed"], "margin_alerts": len(pc["margin_alerts"])}}


def article_problems(rows: list[dict]) -> list[str]:
    """AI-style checks on the master itself: prices that cannot be right, missing prices, duplicates."""
    out = []
    no_dp = [r["name"] for r in rows if not _f(r.get("purchase_price"))]
    if no_dp:
        out.append(f"{len(no_dp)} article(s) have no buying price (DP) — profit and CI checks can't work for them: "
                   + ", ".join(no_dp[:4]))
    no_mrp = [r["name"] for r in rows if not _f(r.get("mrp"))]
    if no_mrp:
        out.append(f"{len(no_mrp)} article(s) have no MRP: " + ", ".join(no_mrp[:4]))
    over = [r["name"] for r in rows if _f(r.get("mrp")) and _f(r.get("selling_price")) > _f(r.get("mrp"))]
    if over:
        out.append(f"{len(over)} article(s) sell above MRP: " + ", ".join(over[:4]))
    loss = [r["name"] for r in rows if _f(r.get("purchase_price")) and _f(r.get("selling_price"))
            and _f(r.get("selling_price")) < _f(r.get("purchase_price"))]
    if loss:
        out.append(f"{len(loss)} article(s) sell below the buying price: " + ", ".join(loss[:4]))
    codes: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r.get("code"):
            codes[_norm(r["code"])].append(r["name"])
    dup = [c for c, v in codes.items() if len(v) > 1]
    if dup:
        out.append(f"{len(dup)} article code(s) are used twice: " + ", ".join(dup[:4]))
    names: dict[str, int] = defaultdict(int)
    for r in rows:
        names[_norm(r.get("name"))] += 1
    same = [n for n, c in names.items() if c > 1 and n]
    if same:
        out.append(f"{len(same)} article name(s) are used twice - tell them apart (size / shade): " + ", ".join(same[:4]))
    vague = [r["name"] for r in rows if r.get("category") and _norm(r.get("name")) == _norm(r.get("category"))]
    if len(vague) > 1:
        out.append(f"{len(vague)} article(s) are named only by their product type (e.g. {vague[0]}) - "
                   "read the company sheet again so each gets its brand, TC and size")
    return out


def price_history(conn: sqlite3.Connection, ws: str, product_id: int) -> list[dict[str, Any]]:
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT field, old_value, new_value, season, source, by_user, at FROM hop_dist_price_history "
        "WHERE workspace_id=? AND product_id=? ORDER BY at DESC, id DESC", (ws, int(product_id))).fetchall()]


def _log_price(conn, ws, pid: int, field: str, old: float, new: float, season: str, source: str, user: str) -> None:
    if abs(_f(old) - _f(new)) < 0.005:
        return
    conn.execute("INSERT INTO hop_dist_price_history (workspace_id, product_id, field, old_value, new_value, season, source, "
                 "by_user, at) VALUES (?,?,?,?,?,?,?,?,?)", (ws, int(pid), field, _f(old) or None, _f(new), season or None,
                                                             source, user or None, _now()))


def save_article(conn: sqlite3.Connection, ws: str, payload: dict, *, user: str = "", source: str = "manual") -> dict[str, Any]:
    """Create or update one article; every price change goes into the price history."""
    ensure_schema(conn)
    name = _s(payload.get("name"))[:160]
    pid = payload.get("id")
    now = _now()
    fields = {k: payload.get(k) for k in ("code", "category", "season", "hsn", "unit") if k in payload}
    details = _details(payload.get("details")) if "details" in payload else None
    prices = {k: payload.get(k) for k in ("mrp", "purchase_price", "selling_price", "gst_pct") if k in payload}
    if pid:
        cur = conn.execute(f"SELECT {_ART_COLS} FROM hop_products WHERE workspace_id=? AND id=?", (ws, int(pid))).fetchone()
        if not cur:
            raise LookupError("Article not found")
        cur = dict(cur)
        sets, vals = [], []
        if name:
            sets.append("name=?"); vals.append(name)
        for k, v in fields.items():
            sets.append(f"{k}=?"); vals.append(_s(v)[:80] or None)
        for k, v in prices.items():
            if v is None or v == "":
                continue
            sets.append(f"{k}=?"); vals.append(_f(v))
            if k != "gst_pct":
                _log_price(conn, ws, int(pid), k, cur.get(k), _f(v), _s(payload.get("season") or cur.get("season")), source, user)
        if details is not None:   # new details are added / updated; details the sheet does not have are kept
            sets.append("custom_fields=?")
            vals.append(json.dumps({**_details(cur.get("custom_fields")), **details}, ensure_ascii=False) or None)
        if sets:
            conn.execute(f"UPDATE hop_products SET {', '.join(sets)}, updated_at=? WHERE workspace_id=? AND id=?",
                         [*vals, now, ws, int(pid)])
        new_id = int(pid)
    else:
        if not name:
            raise ValueError("Type the article name")
        code = _s(payload.get("code"))
        if code and conn.execute("SELECT 1 FROM hop_products WHERE workspace_id=? AND lower(code)=lower(?) "
                                 "AND (deleted_at IS NULL OR trim(deleted_at)='')", (ws, code)).fetchone():
            raise ValueError(f"Article code {code} is already in the master")
        c = conn.execute(
            "INSERT INTO hop_products (workspace_id, code, name, category, season, hsn, unit, mrp, purchase_price, selling_price, "
            "gst_pct, stock_qty, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,'active',?,?)",
            (ws, code or None, name, _s(fields.get("category")) or None, _s(fields.get("season")) or None,
             _s(fields.get("hsn")) or None, _s(fields.get("unit")) or "Pcs", _f(prices.get("mrp")) or None,
             _f(prices.get("purchase_price")) or None, _f(prices.get("selling_price")) or None, _f(prices.get("gst_pct")), now, now))
        new_id = int(c.lastrowid)
        if details:
            conn.execute("UPDATE hop_products SET custom_fields=? WHERE id=?", (json.dumps(details, ensure_ascii=False), new_id))
        for k in ("mrp", "purchase_price", "selling_price"):
            if _f(prices.get(k)):
                _log_price(conn, ws, new_id, k, 0, _f(prices.get(k)), _s(fields.get("season")), source, user)
    conn.commit()
    r = conn.execute(f"SELECT {_ART_COLS} FROM hop_products WHERE workspace_id=? AND id=?", (ws, new_id)).fetchone()
    return _art(dict(r))


def _match_article(conn, ws, code: str, name: str) -> dict | None:
    rows = [dict(r) for r in conn.execute(f"SELECT {_ART_COLS} FROM hop_products WHERE workspace_id=? "
                                          "AND (deleted_at IS NULL OR trim(deleted_at)='')", (ws,)).fetchall()]
    if code:
        for r in rows:
            if r.get("code") and _norm(r["code"]) == _norm(code):
                return r
    best = max(rows, key=lambda r: _sim(name, r["name"]), default=None)
    return best if best and _sim(name, best["name"]) >= 0.86 else None


def price_list_preview(conn: sqlite3.Connection, ws: str, rows: list[dict]) -> dict[str, Any]:
    """Company price list rows → what would change in the Article Master (nothing is saved here)."""
    ensure_schema(conn)
    out = []
    for raw in rows[:1000]:
        name, code = _s(raw.get("name") or raw.get("item"))[:160], _s(raw.get("code"))[:60]
        if not name and not code:
            continue
        new = {k: _f(raw.get(k)) for k in ("mrp", "purchase_price", "selling_price") if _f(raw.get(k))}
        extra = {"details": _details(raw.get("details")), "checks": list(raw.get("checks") or []),
                 **({"gst_pct": _f(raw.get("gst_pct"))} if _f(raw.get("gst_pct")) else {}),
                 **{k: _s(raw.get(k))[:80] for k in ("hsn", "unit") if _s(raw.get(k))}}
        m = _match_article(conn, ws, code, name)
        if not m:
            out.append({"action": "new", "code": code, "name": name, "category": _s(raw.get("category")) or None,
                        "season": _s(raw.get("season")) or None, **new, **extra, "changes": []})
            continue
        changes = [{"field": k, "old": _f(m.get(k)), "new": v} for k, v in new.items() if abs(_f(m.get(k)) - v) >= 0.005]
        warn = [f"{c['field'].replace('_', ' ')} {('up' if c['new'] > c['old'] else 'down')} "
                f"{abs(c['new'] - c['old']) / c['old'] * 100:.0f}%" for c in changes if c["old"] and abs(c["new"] - c["old"]) / c["old"] > 0.25]
        new_details = [k for k, v in extra["details"].items() if _details(m.get("custom_fields")).get(k) != v]
        out.append({"action": "change" if (changes or new_details) else "same", "id": m["id"], "code": code or m.get("code"),
                    "name": m["name"], "category": _s(raw.get("category")) or m.get("category"),
                    "season": _s(raw.get("season")) or m.get("season"), **new, **extra, "changes": changes,
                    "new_details": new_details,
                    "check": ("Big change: " + ", ".join(warn) + " — check the price list") if warn else None})
    return {"rows": out, "new": sum(1 for r in out if r["action"] == "new"),
            "changed": sum(1 for r in out if r["action"] == "change"), "same": sum(1 for r in out if r["action"] == "same")}


def price_list_apply(conn: sqlite3.Connection, ws: str, rows: list[dict], *, user: str = "") -> dict[str, Any]:
    done = {"created": 0, "updated": 0}
    for r in rows[:1000]:
        if r.get("action") == "new":
            save_article(conn, ws, {k: r.get(k) for k in ("code", "name", "category", "season", "mrp", "purchase_price",
                                                          "selling_price", "gst_pct", "hsn", "unit", "details")
                                    if r.get(k) not in (None, "", {})},
                         user=user, source="price_list")
            done["created"] += 1
        elif r.get("action") == "change" and r.get("id"):
            save_article(conn, ws, {"id": r["id"], "season": r.get("season"),
                                    **{k: r[k] for k in ("mrp", "purchase_price", "selling_price") if r.get(k)},
                                    **({"category": r["category"]} if r.get("category") else {}),
                                    **({"details": r["details"]} if r.get("details") else {})},
                         user=user, source="price_list")
            done["updated"] += 1
    return done


def read_price_list_excel(data: bytes) -> list[dict]:
    """Excel price list without AI: finds the header row (name / code / MRP / DP / rate / category / season)."""
    import io

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    keys = {"name": ("article", "item", "description", "name", "product", "quality", "design"),
            "code": ("code", "art no", "article no", "sku", "item code"),
            "mrp": ("mrp",), "purchase_price": ("dp", "dealer", "distributor", "nlc", "net rate", "cost", "billing"),
            "selling_price": ("retailer", "selling", "wholesale", "rlp", "sale rate"),
            "category": ("category", "group", "range"), "season": ("season",)}
    rows = list(ws.iter_rows(values_only=True))
    hdr_i, colmap = None, {}
    for i, row in enumerate(rows[:30]):
        cm = {}
        for j, cell in enumerate(row or []):
            h = _norm(cell)
            for k, words in keys.items():
                if k not in cm and any(h == w or h.startswith(w) or f" {w}" in f" {h}" for w in words):
                    cm[k] = j
        if "name" in cm and ("mrp" in cm or "purchase_price" in cm):
            hdr_i, colmap = i, cm
            break
    if hdr_i is None:
        raise ValueError("Could not find the header row (need an article / item name and MRP or DP column)")
    out = []
    for row in rows[hdr_i + 1:]:
        if not row:
            continue
        d = {k: row[j] if j < len(row) else None for k, j in colmap.items()}
        if not _s(d.get("name")):
            continue
        out.append({k: (_s(v) if k in ("name", "code", "category", "season") else _f(v)) for k, v in d.items()})
    return out


# ══ AI entry checks ═══════════════════════════════════════════════════════════════════════════════════════
def _lines_of(conn, ws, txn_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT l.item_name, l.product_id, ABS(COALESCE(l.qty,0)) AS qty, COALESCE(l.rate,0) AS rate, COALESCE(l.tax_pct,0) AS tax_pct
           FROM hop_party_transactions t JOIN hop_txn_lines l ON l.workspace_id=t.workspace_id AND l.source_txn_id=t.source_txn_id
           WHERE t.workspace_id=? AND t.id=?""", (ws, int(txn_id))).fetchall()]


def _key(conn, ws, ln: dict) -> tuple[str, dict | None]:
    art = None
    if ln.get("product_id"):
        r = conn.execute(f"SELECT {_ART_COLS} FROM hop_products WHERE workspace_id=? AND id=?", (ws, int(ln["product_id"]))).fetchone()
        art = dict(r) if r else None
    if not art:
        art = _match_article(conn, ws, _s(ln.get("code")), _s(ln.get("item_name") or ln.get("name")))
    return (f"p{art['id']}" if art else "n" + _norm(ln.get("item_name") or ln.get("name"))), art


def check_lines(conn: sqlite3.Connection, ws: str, lines: list[dict], *, against: list[dict] | None = None,
                against_label: str = "the order", rate_field: str = "purchase_price") -> list[str]:
    """Each line vs the Article Master (unknown article, rate ≠ DP, GST ≠ master) and vs an earlier document
    (item not there, more qty than there, different rate)."""
    ensure_schema(conn)
    warns: list[str] = []
    base: dict[str, dict] = {}
    for ln in against or []:
        k, _a = _key(conn, ws, ln)
        b = base.setdefault(k, {"qty": 0.0, "rate": _f(ln.get("rate")), "name": ln.get("item_name")})
        b["qty"] += _f(ln.get("qty"))
    for ln in lines or []:
        name = _s(ln.get("item_name") or ln.get("name")) or "Item"
        qty, rate = _f(ln.get("qty")), _f(ln.get("rate"))
        k, art = _key(conn, ws, ln)
        if not art:
            warns.append(f"“{name}” is not in your Article Master — check the name/code or add the article first.")
        else:
            ref = _f(art.get(rate_field))
            if ref and rate and abs(rate - ref) / ref > 0.02:
                warns.append(f"{art['name']}: rate ₹{rate:,.2f} but the Article Master says ₹{ref:,.2f} "
                             f"({'more' if rate > ref else 'less'} by {abs(rate - ref) / ref * 100:.0f}%).")
            gst = ln.get("tax_pct")
            if gst not in (None, "") and art.get("gst_pct") not in (None, "") and abs(_f(gst) - _f(art["gst_pct"])) > 0.01 \
                    and _f(art["gst_pct"]) > 0:
                warns.append(f"{art['name']}: GST {_f(gst):g}% but the article has {_f(art['gst_pct']):g}%.")
        if against is not None:
            b = base.get(k)
            if not b:
                warns.append(f"{name} is not in {against_label}.")
            else:
                if qty > b["qty"] + 1e-6:
                    warns.append(f"{name}: {qty:g} here but only {b['qty']:g} in {against_label}.")
                if b["rate"] and rate and abs(rate - b["rate"]) / b["rate"] > 0.02:
                    warns.append(f"{name}: rate ₹{rate:,.2f} here, ₹{b['rate']:,.2f} in {against_label}.")
    return warns


def check_so(conn: sqlite3.Connection, ws: str, payload: dict) -> dict[str, Any]:
    """Before saving a company SO: vs my order (items, qty, rate, dates) and the Article Master."""
    from app import hop_distributor as D

    D.ensure_schema(conn)
    warns: list[str] = []
    so_no = _s(payload.get("so_number"))
    if so_no and conn.execute("SELECT 1 FROM hop_dist_so WHERE workspace_id=? AND lower(so_number)=lower(?) AND deleted_at IS NULL",
                              (ws, so_no)).fetchone():
        warns.append(f"SO {so_no} is already saved — this may be a duplicate.")
    lines = payload.get("lines") or []
    po = payload.get("po_txn_id")
    against = None
    if po:
        r = conn.execute("SELECT txn_number, txn_date FROM hop_party_transactions WHERE workspace_id=? AND id=?", (ws, int(po))).fetchone()
        if r:
            against = _lines_of(conn, ws, int(po))
            if payload.get("so_date") and r[1] and str(payload["so_date"])[:10] < str(r[1])[:10]:
                warns.append(f"SO date {payload['so_date']} is before my order {r[0]} ({str(r[1])[:10]}).")
    warns += check_lines(conn, ws, lines, against=against, against_label="my order")
    total = _f(payload.get("amount"))
    calc = sum(_f(ln.get("qty")) * _f(ln.get("rate")) for ln in lines)
    if total and calc and abs(total - calc) > max(2.0, total * 0.02) and abs(total - calc * 1.05) > max(2.0, total * 0.02) \
            and abs(total - calc * 1.12) > max(2.0, total * 0.02) and abs(total - calc * 1.18) > max(2.0, total * 0.02):
        warns.append(f"Lines add up to ₹{calc:,.2f} (before GST) but the SO amount is ₹{total:,.2f}.")
    return {"warnings": warns}


def check_ci(conn: sqlite3.Connection, ws: str, payload: dict) -> list[str]:
    """A purchase bill from a principal = the company's invoice: vs its SO / order and the Article Master."""
    from app import hop_distributor as D

    D.ensure_schema(conn)
    vid = payload.get("vendor_id") or (payload.get("party_id") if payload.get("party_type") == "vendor" else None)
    if not vid or not conn.execute("SELECT 1 FROM hop_vendors WHERE workspace_id=? AND id=? AND is_principal=1",
                                   (ws, int(vid))).fetchone():
        return []
    warns: list[str] = []
    ci_no = _s(payload.get("party_bill_no"))
    if ci_no:
        dup = conn.execute(f"""SELECT txn_number FROM hop_party_transactions t WHERE t.workspace_id=? AND t.txn_type=2 AND {LIVE}
                               AND t.party_id=? AND lower(COALESCE(t.party_bill_no,''))=lower(?) AND t.id != ?""",
                           (ws, int(vid), ci_no, int(payload.get("exclude_txn_id") or 0))).fetchone()
        if dup:
            warns.append(f"Company invoice {ci_no} is already saved as {dup[0]} — duplicate?")
    src = payload.get("linked_from_txn_id")
    against, label = None, "the order"
    if src:
        against = _lines_of(conn, ws, int(src))
    elif payload.get("so_id"):
        so = D.get_so(conn, ws, int(payload["so_id"]))
        against, label = so.get("lines") or None, f"SO {so['so_number']}"
    warns += check_lines(conn, ws, payload.get("lines") or [], against=against, against_label=label)
    return warns


def ledger_check(conn: sqlite3.Connection, ws: str, vendor_id: int, rows: list[dict]) -> dict[str, Any]:
    """The company's ledger (SOA) rows [{date, doc_no, debit, credit, narration}] vs my books with that company.
    In the company's ledger my account is the customer: their debit = their invoice to me, credit = my payment."""
    bills = [dict(r) for r in conn.execute(f"""SELECT t.id, t.txn_number, t.party_bill_no, t.txn_date, t.total_amount
        FROM hop_party_transactions t WHERE t.workspace_id=? AND t.txn_type=2 AND {LIVE} AND t.party_type='vendor' AND t.party_id=?""",
                                           (ws, int(vendor_id))).fetchall()]
    pays = [dict(r) for r in conn.execute(f"""SELECT t.id, t.txn_number, t.txn_date, t.total_amount, t.payment_ref
        FROM hop_party_transactions t WHERE t.workspace_id=? AND t.txn_type=4 AND {LIVE} AND t.party_type='vendor' AND t.party_id=?""",
                                          (ws, int(vendor_id))).fetchall()]
    used_b, used_p = set(), set()
    missing_bills, missing_pays, amount_diff = [], [], []
    for r in rows:
        dr, cr = _f(r.get("debit")), _f(r.get("credit"))
        doc = _norm(r.get("doc_no"))
        d = _s(r.get("date"))[:10]
        if dr > 0:
            hit = next((b for b in bills if b["id"] not in used_b and doc and _norm(b.get("party_bill_no")) == doc), None) \
                or next((b for b in bills if b["id"] not in used_b and abs(_f(b["total_amount"]) - dr) < 1.0
                         and _near(b["txn_date"], d, 5)), None)
            if not hit:
                missing_bills.append({"date": d, "doc_no": r.get("doc_no"), "amount": dr, "narration": r.get("narration")})
            else:
                used_b.add(hit["id"])
                if abs(_f(hit["total_amount"]) - dr) >= 1.0:
                    amount_diff.append({"doc_no": r.get("doc_no"), "company_says": dr, "my_books": _f(hit["total_amount"]),
                                        "txn_id": hit["id"]})
        elif cr > 0:
            hit = next((p for p in pays if p["id"] not in used_p and abs(_f(p["total_amount"]) - cr) < 1.0
                        and _near(p["txn_date"], d, 10)), None)
            if not hit:
                missing_pays.append({"date": d, "doc_no": r.get("doc_no"), "amount": cr, "narration": r.get("narration")})
            else:
                used_p.add(hit["id"])
    only_mine_bills = [{"txn_id": b["id"], "ci": b.get("party_bill_no") or b["txn_number"], "date": b["txn_date"],
                        "amount": b["total_amount"]} for b in bills if b["id"] not in used_b]
    only_mine_pays = [{"txn_id": p["id"], "number": p["txn_number"], "date": p["txn_date"], "amount": p["total_amount"]}
                      for p in pays if p["id"] not in used_p]
    ok = not (missing_bills or missing_pays or amount_diff)
    return {"matched_bills": len(used_b), "matched_payments": len(used_p), "ok": ok,
            "in_company_ledger_not_in_my_books": {"invoices": missing_bills, "payments_or_credits": missing_pays},
            "amount_differs": amount_diff,
            "in_my_books_not_in_company_ledger": {"invoices": only_mine_bills, "payments": only_mine_pays}}


def _near(a: Any, b: Any, days: int) -> bool:
    try:
        return abs((date.fromisoformat(str(a)[:10]) - date.fromisoformat(str(b)[:10])).days) <= days
    except ValueError:
        return False


# ══ Retailer payment + follow-up ══════════════════════════════════════════════════════════════════════════
def add_followup(conn: sqlite3.Connection, ws: str, payload: dict, *, user: str = "") -> dict[str, Any]:
    ensure_schema(conn)
    pid = int(payload.get("party_id") or 0)
    if not conn.execute("SELECT 1 FROM hop_customers WHERE workspace_id=? AND id=?", (ws, pid)).fetchone():
        raise LookupError("Retailer not found")
    note = _s(payload.get("note"))[:1000]
    pd = _s(payload.get("promise_date"))[:10]
    if not note and not pd:
        raise ValueError("Write what happened (or the promised date)")
    now = _now()
    conn.execute("INSERT INTO hop_dist_followups (workspace_id, party_id, at, mode, note, promise_date, promise_amount, by_user, created_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?)", (ws, pid, _s(payload.get("at"))[:10] or _today().isoformat(),
                                                _s(payload.get("mode"))[:20] or "call", note or None, pd or None,
                                                _f(payload.get("promise_amount")) or None, user or _s(payload.get("by"))[:60] or None, now))
    conn.commit()
    return retailer_detail(conn, ws, pid)


def _followups(conn, ws, pid: int | None = None) -> list[dict]:
    sql, args = "SELECT * FROM hop_dist_followups WHERE workspace_id=?", [ws]
    if pid:
        sql += " AND party_id=?"; args.append(int(pid))
    return [dict(r) for r in conn.execute(sql + " ORDER BY at DESC, id DESC", args).fetchall()]


def _promise_state(f: dict, payments: list[dict]) -> str | None:
    if not f.get("promise_date"):
        return None
    after = sum(_f(p["total_amount"]) for p in payments if str(p["txn_date"])[:10] >= str(f["at"])[:10])
    need = _f(f.get("promise_amount"))
    if after > 0 and (not need or after >= need - 1):
        return "kept"
    return "broken" if str(f["promise_date"]) < _today().isoformat() else "open"


def retailer_dues(conn: sqlite3.Connection, ws: str) -> dict[str, Any]:
    """Every retailer with money due: overdue age, last payment, follow-ups, promises, and who to call first."""
    ensure_schema(conn)
    today = _today()
    bills = [dict(r) for r in conn.execute(f"""SELECT t.id, t.party_id, t.txn_number, t.txn_date, t.due_date, t.total_amount, t.balance_amount,
               COALESCE(c.company, t.party_name, '') AS party, c.mobile, COALESCE(c.credit_days, 0) AS credit_days
        FROM hop_party_transactions t LEFT JOIN hop_customers c ON c.workspace_id=t.workspace_id AND c.id=t.party_id
        WHERE t.workspace_id=? AND t.txn_type=1 AND {LIVE} AND t.party_type='customer' AND t.balance_amount > 0.009
        ORDER BY date(t.txn_date)""", (ws,)).fetchall()] if _has_col(conn, "hop_customers", "credit_days") else \
        [dict(r) for r in conn.execute(f"""SELECT t.id, t.party_id, t.txn_number, t.txn_date, t.due_date, t.total_amount, t.balance_amount,
               COALESCE(c.company, t.party_name, '') AS party, c.mobile, 0 AS credit_days
        FROM hop_party_transactions t LEFT JOIN hop_customers c ON c.workspace_id=t.workspace_id AND c.id=t.party_id
        WHERE t.workspace_id=? AND t.txn_type=1 AND {LIVE} AND t.party_type='customer' AND t.balance_amount > 0.009
        ORDER BY date(t.txn_date)""", (ws,)).fetchall()]
    pays = defaultdict(list)
    for r in conn.execute(f"""SELECT t.party_id, t.txn_date, t.total_amount FROM hop_party_transactions t WHERE t.workspace_id=?
                              AND t.txn_type=3 AND {LIVE} AND t.party_type='customer' ORDER BY date(t.txn_date) DESC""", (ws,)):
        pays[int(r[0] or 0)].append({"txn_date": r[1], "total_amount": r[2]})
    fus = defaultdict(list)
    for f in _followups(conn, ws):
        fus[int(f["party_id"])].append(f)
    by: dict[int, dict[str, Any]] = {}
    for b in bills:
        pid = int(b["party_id"] or 0)
        due_on = _s(b.get("due_date"))[:10] or (date.fromisoformat(str(b["txn_date"])[:10]) + timedelta(days=int(b["credit_days"] or 0))).isoformat()
        late = max(0, (today - date.fromisoformat(due_on)).days)
        r = by.setdefault(pid, {"party_id": pid, "party": b["party"], "mobile": b.get("mobile"), "due": 0.0, "overdue": 0.0,
                                "bills": 0, "oldest_days": 0, "buckets": {"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}})
        bal = _f(b["balance_amount"])
        r["due"] += bal
        r["bills"] += 1
        if late > 0:
            r["overdue"] += bal
        r["oldest_days"] = max(r["oldest_days"], late)
        r["buckets"]["0-30" if late <= 30 else "31-60" if late <= 60 else "61-90" if late <= 90 else "90+"] += bal
    out = []
    for pid, r in by.items():
        p = pays.get(pid, [])
        f = fus.get(pid, [])
        states = [(_promise_state(x, p), x) for x in f]
        broken = sum(1 for s, _ in states if s == "broken")
        open_p = next((x for s, x in states if s == "open"), None)
        last_fu = f[0] if f else None
        days_since_fu = (today - date.fromisoformat(str(last_fu["at"])[:10])).days if last_fu else None
        score = r["overdue"] / 1000 * (1 + r["oldest_days"] / 30) + broken * 20 + (10 if days_since_fu is None or days_since_fu > 7 else 0)
        reasons = []
        if r["overdue"]:
            reasons.append(f"{_inr(r['overdue'])} overdue, oldest {_days(r['oldest_days'])}")
        if broken:
            reasons.append(f"{broken} promise(s) broken")
        if open_p:
            reasons.append(f"promised {_inr(_f(open_p.get('promise_amount')))} by {open_p['promise_date']}")
            if str(open_p["promise_date"]) > today.isoformat():
                score *= 0.4           # wait for the promise date
        if days_since_fu is None:
            reasons.append("never followed up")
        elif days_since_fu > 7:
            reasons.append(f"last follow-up {_days(days_since_fu)} ago")
        out.append({**r, "due": _r2(r["due"]), "overdue": _r2(r["overdue"]),
                    "buckets": {k: _r2(v) for k, v in r["buckets"].items()},
                    "last_payment": p[0] if p else None, "last_followup": last_fu, "open_promise": open_p,
                    "broken_promises": broken, "priority": round(score, 1), "why": "; ".join(reasons)})
    out.sort(key=lambda x: -x["priority"])
    call_today = [x for x in out if x["overdue"] > 0 and not (x["open_promise"] and str(x["open_promise"]["promise_date"]) > today.isoformat())][:10]
    return {"retailers": out, "total_due": _r2(sum(x["due"] for x in out)), "total_overdue": _r2(sum(x["overdue"] for x in out)),
            "call_today": [{"party_id": x["party_id"], "party": x["party"], "mobile": x["mobile"], "why": x["why"],
                            "overdue": x["overdue"]} for x in call_today]}


def _has_col(conn, table: str, col: str) -> bool:
    return col in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def retailer_detail(conn: sqlite3.Connection, ws: str, party_id: int) -> dict[str, Any]:
    ensure_schema(conn)
    all_d = retailer_dues(conn, ws)
    row = next((r for r in all_d["retailers"] if int(r["party_id"]) == int(party_id)), None)
    c = conn.execute("SELECT id, company, mobile FROM hop_customers WHERE workspace_id=? AND id=?", (ws, int(party_id))).fetchone()
    if not c:
        raise LookupError("Retailer not found")
    bills = [dict(r) for r in conn.execute(f"""SELECT t.id, t.txn_number, t.txn_date, t.due_date, t.total_amount, t.balance_amount
        FROM hop_party_transactions t WHERE t.workspace_id=? AND t.txn_type=1 AND {LIVE} AND t.party_id=? AND t.party_type='customer'
        AND t.balance_amount > 0.009 ORDER BY date(t.txn_date)""", (ws, int(party_id))).fetchall()]
    pays = [dict(r) for r in conn.execute(f"""SELECT t.id, t.txn_number, t.txn_date, t.total_amount, t.payment_mode FROM hop_party_transactions t
        WHERE t.workspace_id=? AND t.txn_type=3 AND {LIVE} AND t.party_id=? AND t.party_type='customer'
        ORDER BY date(t.txn_date) DESC LIMIT 20""", (ws, int(party_id))).fetchall()]
    fu = _followups(conn, ws, int(party_id))
    for f in fu:
        f["promise_state"] = _promise_state(f, pays)
    msg = (f"Namaste {c['company']} ji, aapka {_inr((row or {}).get('due', 0))} ka payment baaki hai"
           + (f" (sabse purana bill {(row or {}).get('oldest_days')} din overdue)" if (row or {}).get("oldest_days") else "")
           + ". Kripya jaldi payment kar dijiye. Dhanyavaad.")
    return {"party_id": int(party_id), "party": c["company"], "mobile": c["mobile"], "summary": row,
            "open_bills": bills, "payments": pays, "followups": fu, "reminder_text": msg}


# ══ Staff performance ═════════════════════════════════════════════════════════════════════════════════════
def staff_performance(conn: sqlite3.Connection, ws: str, frm: str = "", to: str = "") -> dict[str, Any]:
    """Per login (from the audit trail): bills and their value, collections, orders to the company, follow-ups."""
    ensure_schema(conn)
    frm = frm or _today().replace(day=1).isoformat()
    to = to or _today().isoformat()
    try:
        logs = [dict(r) for r in conn.execute(
            "SELECT user_name, action, entity, entity_id, path, at FROM hop_audit_log WHERE workspace_id=? "
            "AND substr(at,1,10) BETWEEN ? AND ?", (ws, frm, to)).fetchall()]
    except sqlite3.OperationalError:
        logs = []
    txn_ids = {}
    for lg in logs:
        path = _s(lg.get("path")).rstrip("/")
        if path.endswith("/party-transactions") and _s(lg.get("entity_id")).isdigit() and lg.get("action") in ("created", "create"):
            txn_ids[int(lg["entity_id"])] = lg.get("user_name") or "Unknown"
    txns = {}
    if txn_ids:
        q = ",".join(str(i) for i in txn_ids)
        for r in conn.execute(f"SELECT id, txn_type, total_amount FROM hop_party_transactions WHERE workspace_id=? AND id IN ({q})", (ws,)):
            txns[int(r[0])] = (int(r[1] or 0), _f(r[2]))
    people: dict[str, dict[str, Any]] = defaultdict(lambda: {"sale_bills": 0, "sales": 0.0, "collections": 0.0, "payments_in": 0,
                                                           "orders_to_company": 0, "challans": 0, "followups": 0, "promises": 0,
                                                           "edits": 0, "deletes": 0, "actions": 0, "active_days": set()})
    for lg in logs:
        u = lg.get("user_name") or "Unknown"
        p = people[u]
        p["actions"] += 1
        p["active_days"].add(str(lg["at"])[:10])
        if lg.get("action") in ("changed", "update"):
            p["edits"] += 1
        if lg.get("action") in ("deleted", "delete"):
            p["deletes"] += 1
    for tid, u in txn_ids.items():
        ty, amt = txns.get(tid, (0, 0.0))
        p = people[u]
        if ty == 1:
            p["sale_bills"] += 1; p["sales"] += amt
        elif ty == 3:
            p["payments_in"] += 1; p["collections"] += amt
        elif ty == 66:
            p["orders_to_company"] += 1
        elif ty == 30:
            p["challans"] += 1
    for f in _followups(conn, ws):
        if frm <= str(f["at"])[:10] <= to:
            p = people[f.get("by_user") or "Unknown"]
            p["followups"] += 1
            if f.get("promise_date"):
                p["promises"] += 1
    out = []
    for u, p in people.items():
        out.append({"staff": u, **{k: (_r2(v) if isinstance(v, float) else v) for k, v in p.items() if k != "active_days"},
                    "active_days": len(p["active_days"])})
    out.sort(key=lambda x: (-x["sales"], -x["collections"]))
    tips = []
    for x in out:
        if x["deletes"] >= 3:
            tips.append(f"{x['staff']} deleted {x['deletes']} entries in this period — check the audit trail.")
        if x["sale_bills"] and not x["followups"] and x["staff"] != "Unknown":
            tips.append(f"{x['staff']} made bills but logged no payment follow-up.")
    return {"from": frm, "to": to, "staff": out, "tips": tips,
            "note": "Counted from the audit trail (who saved each entry) and the follow-up log."}


# ══ Profitability ═════════════════════════════════════════════════════════════════════════════════════════
def profitability(conn: sqlite3.Connection, ws: str, frm: str = "", to: str = "") -> dict[str, Any]:
    """Sales, cost of goods (qty × the article's buying price), gross profit, expenses and net — by month, item,
    retailer and company (principal)."""
    from app import hop_distributor as D

    D.ensure_schema(conn)
    today = _today()
    frm = frm or (date(today.year if today.month >= 4 else today.year - 1, 4, 1)).isoformat()
    to = to or today.isoformat()
    arts = {int(r["id"]): dict(r) for r in conn.execute(
        "SELECT id, name, purchase_price, vendor_id FROM hop_products WHERE workspace_id=?", (ws,)).fetchall()}
    by_name = {_norm(a["name"]): a for a in arts.values()}
    principals = {int(p["id"]): p["company"] for p in D.principals(conn, ws)}
    lines = conn.execute(f"""SELECT t.id, t.txn_type, t.txn_date, t.party_id, COALESCE(c.company, t.party_name, '') AS party,
               l.item_name, l.product_id, ABS(COALESCE(l.qty,0)) AS qty,
               COALESCE(l.line_total,0) - COALESCE(l.tax_amount,0) AS taxable, COALESCE(l.rate,0) AS rate
        FROM hop_party_transactions t
        JOIN hop_txn_lines l ON l.workspace_id=t.workspace_id AND l.source_txn_id=t.source_txn_id
        LEFT JOIN hop_customers c ON c.workspace_id=t.workspace_id AND c.id=t.party_id
        WHERE t.workspace_id=? AND t.txn_type IN (1,21) AND {LIVE} AND COALESCE(l.is_charge,0)=0
          AND date(t.txn_date) BETWEEN date(?) AND date(?)""", (ws, frm, to)).fetchall()
    month = defaultdict(lambda: {"sales": 0.0, "cost": 0.0, "expenses": 0.0})
    item = defaultdict(lambda: {"qty": 0.0, "sales": 0.0, "cost": 0.0, "no_cost": False})
    party = defaultdict(lambda: {"sales": 0.0, "cost": 0.0, "party": ""})
    comp = defaultdict(lambda: {"sales": 0.0, "cost": 0.0})
    below_cost = []
    for r in lines:
        r = dict(r)
        sign = -1 if int(r["txn_type"]) == 21 else 1
        a = arts.get(int(r["product_id"])) if r.get("product_id") else by_name.get(_norm(r["item_name"]))
        cost_each = _f(a.get("purchase_price")) if a else 0.0
        sales, cost = sign * _f(r["taxable"]), sign * _f(r["qty"]) * cost_each
        m = str(r["txn_date"])[:7]
        month[m]["sales"] += sales; month[m]["cost"] += cost
        nm = (a["name"] if a else (r["item_name"] or "Item")).strip()
        it = item[nm]
        it["qty"] += sign * _f(r["qty"]); it["sales"] += sales; it["cost"] += cost
        if not cost_each:
            it["no_cost"] = True
        pp = party[int(r["party_id"] or 0)]
        pp["party"] = r["party"]; pp["sales"] += sales; pp["cost"] += cost
        cname = principals.get(int(a.get("vendor_id") or 0)) if a else None
        cname = cname or (next(iter(principals.values())) if len(principals) == 1 else "Other suppliers")
        comp[cname]["sales"] += sales; comp[cname]["cost"] += cost
        if sign > 0 and cost_each and _f(r["qty"]) and _f(r["taxable"]) / _f(r["qty"]) < cost_each - 0.005:
            below_cost.append({"txn_id": r["id"], "item": nm, "party": r["party"], "date": r["txn_date"],
                               "sold_at": _r2(_f(r["taxable"]) / _f(r["qty"])), "cost": _r2(cost_each)})
    for r in conn.execute(f"""SELECT substr(t.txn_date,1,7), SUM(t.total_amount) FROM hop_party_transactions t WHERE t.workspace_id=?
                              AND t.txn_type=7 AND {LIVE} AND date(t.txn_date) BETWEEN date(?) AND date(?) GROUP BY 1""", (ws, frm, to)):
        month[r[0]]["expenses"] += _f(r[1])

    def gp(d: dict) -> dict:
        g = d["sales"] - d["cost"]
        return {**{k: (_r2(v) if isinstance(v, float) else v) for k, v in d.items()}, "gross_profit": _r2(g),
                "margin_pct": round(g / d["sales"] * 100, 1) if d["sales"] else None}

    months = [{"month": m, **gp(v), "net": _r2(v["sales"] - v["cost"] - v["expenses"])} for m, v in sorted(month.items())]
    items = sorted(({"item": k, **gp(v)} for k, v in item.items()), key=lambda x: -x["gross_profit"])
    for i in items:
        if i.get("no_cost"):
            i["margin_pct"] = None     # no buying price → margin unknown, not 100%
    parties = sorted(({"party_id": k, **gp(v)} for k, v in party.items()), key=lambda x: -x["gross_profit"])
    comps = sorted(({"company": k, **gp(v)} for k, v in comp.items()), key=lambda x: -x["gross_profit"])
    tot_s = sum(m["sales"] for m in months)
    tot_c = sum(m["cost"] for m in months)
    tot_e = sum(m["expenses"] for m in months)
    return {"from": frm, "to": to,
            "totals": {"sales": _r2(tot_s), "cost": _r2(tot_c), "gross_profit": _r2(tot_s - tot_c),
                       "margin_pct": round((tot_s - tot_c) / tot_s * 100, 1) if tot_s else None,
                       "expenses": _r2(tot_e), "net_profit": _r2(tot_s - tot_c - tot_e)},
            "months": months, "items": items[:60], "low_margin_items": [i for i in items if i["margin_pct"] is not None
                                                                         and i["margin_pct"] < 5][:20],
            "items_without_cost": [i["item"] for i in items if i.get("no_cost")][:20],
            "retailers": parties[:40], "companies": comps, "sold_below_cost": below_cost[:30],
            "note": "Cost = qty × the article's buying price (DP) in the Article Master. Sales are before GST."}


def read_ledger_excel(data: bytes) -> list[dict]:
    """Company ledger in Excel without AI: finds the date / document / debit / credit columns."""
    import io

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    rows = list(wb.worksheets[0].iter_rows(values_only=True))
    want = {"date": ("date", "posting date", "doc date", "document date"),
            "doc_no": ("document no", "doc no", "invoice no", "reference", "ref", "assignment", "bill no", "voucher no"),
            "debit": ("debit", "dr"), "credit": ("credit", "cr"), "narration": ("narration", "particulars", "text", "description")}
    hdr, cm = None, {}
    for i, row in enumerate(rows[:40]):
        m = {}
        for j, cell in enumerate(row or []):
            h = _norm(cell)
            for k, words in want.items():
                if k not in m and any(h == w or h.startswith(w + " ") or h == w + "." for w in words):
                    m[k] = j
        if "date" in m and ("debit" in m or "credit" in m):
            hdr, cm = i, m
            break
    if hdr is None:
        raise ValueError("Could not find the Date / Debit / Credit columns — send the ledger as PDF or photo")
    out = []
    for row in rows[hdr + 1:]:
        if not row:
            continue
        d = row[cm["date"]] if cm["date"] < len(row) else None
        ds = d.date().isoformat() if hasattr(d, "date") else _s(d)[:10]
        dr = _f(row[cm["debit"]]) if "debit" in cm and cm["debit"] < len(row) else 0.0
        cr = _f(row[cm["credit"]]) if "credit" in cm and cm["credit"] < len(row) else 0.0
        if not ds or (not dr and not cr):
            continue
        out.append({"date": ds, "doc_no": _s(row[cm["doc_no"]]) if "doc_no" in cm and cm["doc_no"] < len(row) else "",
                    "narration": _s(row[cm["narration"]])[:160] if "narration" in cm and cm["narration"] < len(row) else "",
                    "debit": dr, "credit": cr})
    return out


# == Price changes: what changed, by how much, what it does to stock value and margin ===================
def price_changes(conn: sqlite3.Connection, ws: str, days: int = 30, field: str = "") -> dict[str, Any]:
    """Every Article Master price change in the last `days`: old -> new, %, source, and its effect: stock value
    (DP change x stock), margin now vs before, and a suggested retailer price that keeps the old margin %."""
    ensure_schema(conn)
    days = max(1, min(int(days or 30), 730))
    since = (_today() - timedelta(days=days)).isoformat()
    sql = """SELECT h.id, h.product_id, h.field, h.old_value, h.new_value, h.season, h.source, h.by_user, h.at,
                    p.code, p.name, p.category, p.stock_qty, p.mrp, p.purchase_price, p.selling_price
             FROM hop_dist_price_history h JOIN hop_products p ON p.workspace_id=h.workspace_id AND p.id=h.product_id
             WHERE h.workspace_id=? AND substr(h.at,1,10) >= ?"""
    args: list[Any] = [ws, since]
    if field in ("mrp", "purchase_price", "selling_price"):
        sql += " AND h.field=?"
        args.append(field)
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY h.at DESC, h.id DESC", args).fetchall()]
    changes, ups, downs, stock_effect = [], 0, 0, 0.0
    by_cat: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        old, new = _f(r["old_value"]), _f(r["new_value"])
        pct = round((new - old) / old * 100, 1) if old else None
        if old and new > old:
            ups += 1
        elif old and new < old:
            downs += 1
        eff = 0.0
        if r["field"] == "purchase_price" and old:
            eff = (new - old) * max(_f(r["stock_qty"]), 0)
            stock_effect += eff
        if pct is not None:
            by_cat[r.get("category") or "Other"].append(pct)
        changes.append({"id": r["id"], "product_id": r["product_id"], "code": r.get("code"), "name": r["name"],
                        "category": r.get("category"), "field": r["field"], "old": old or None, "new": new, "pct": pct,
                        "season": r.get("season"), "source": r.get("source"), "by": r.get("by_user"), "at": r["at"],
                        "stock_effect": _r2(eff) if eff else None, "first_price": not old})
    margin_alerts = []
    seen: set[int] = set()
    for c in changes:
        if c["field"] != "purchase_price" or not c["old"] or c["product_id"] in seen:
            continue
        seen.add(c["product_id"])
        p = conn.execute("SELECT name, code, purchase_price, selling_price, mrp FROM hop_products WHERE workspace_id=? AND id=?",
                         (ws, c["product_id"])).fetchone()
        if not p:
            continue
        dp, sell, mrp = _f(p["purchase_price"]), _f(p["selling_price"]), _f(p["mrp"])
        if not sell:
            continue
        if conn.execute("SELECT 1 FROM hop_dist_price_history WHERE workspace_id=? AND product_id=? AND field='selling_price' "
                        "AND id > ?", (ws, c["product_id"], c["id"])).fetchone():
            continue          # the retailer price was already updated after this DP change
        old_margin = (sell - c["old"]) / sell * 100
        new_margin = (sell - dp) / sell * 100
        if new_margin >= old_margin - 0.5:
            continue
        keep = round(dp / (1 - old_margin / 100)) if old_margin < 100 else round(sell)
        capped = bool(mrp and keep >= mrp)
        if capped:
            keep = round(mrp)
        margin_alerts.append({"product_id": c["product_id"], "name": p["name"], "code": p["code"], "dp": dp,
                              "selling_price": sell, "mrp": mrp or None, "old_margin_pct": round(old_margin, 1),
                              "new_margin_pct": round(new_margin, 1), "suggested_selling_price": keep, "capped_at_mrp": capped,
                              "why": f"DP went up {c['pct']}% but your price to retailers is still {_inr(sell)}: margin fell "
                                     f"from {old_margin:.1f}% to {new_margin:.1f}%."})
    cats = [{"category": k, "changes": len(v), "avg_pct": round(sum(v) / len(v), 1)} for k, v in by_cat.items()]
    cats.sort(key=lambda x: -abs(x["avg_pct"]))
    latest: dict[int, dict] = {}
    for c in changes:
        latest.setdefault(int(c["product_id"]), c)
    return {"days": days, "since": since, "count": len(changes), "ups": ups, "downs": downs,
            "articles_changed": len(latest), "stock_value_effect": _r2(stock_effect), "categories": cats,
            "changes": changes[:300], "margin_alerts": margin_alerts}


def latest_change_by_article(conn: sqlite3.Connection, ws: str, days: int = 60) -> dict[int, dict]:
    since = (_today() - timedelta(days=days)).isoformat()
    out: dict[int, dict] = {}
    for r in conn.execute("""SELECT product_id, field, old_value, new_value, at FROM hop_dist_price_history
                             WHERE workspace_id=? AND substr(at,1,10) >= ? AND old_value IS NOT NULL AND old_value > 0
                             ORDER BY at DESC, id DESC""", (ws, since)):
        pid = int(r[0])
        if pid in out:
            continue
        old, new = _f(r[2]), _f(r[3])
        out[pid] = {"field": r[1], "old": old, "new": new, "pct": round((new - old) / old * 100, 1) if old else None, "at": r[4]}
    return out

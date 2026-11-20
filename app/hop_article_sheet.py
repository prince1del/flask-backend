"""Article Master from ANY company sheet (owner 20-11: "har article master main kai saare column hote hai").

Company price lists / booking sheets differ every time: 14, 18, 24 columns, the header in row 2 or row 7,
section rows ("Fleece Collection"), blank cells under a merged brand, order-quantity columns mixed in.

* ``analyze`` finds the header row, lists every column with 3 sample values and a guessed ROLE.
* Roles: a few fixed fields (code, name part, category, season, MRP, DP, price to retailer, GST, HSN, unit)
  — every other column is a **detail** kept on the article (``hop_products.custom_fields``), never dropped.
  Order columns (qty, booking, delivery) are skipped by default.
* ``build`` turns the rows into articles with the user's (or remembered) column roles: the name is built from the
  name-part columns ("Cardinal 120 TC DB BS 224 X 254"), section rows become a "Collection" detail, blank text
  cells are filled from the row above.
* The column roles are remembered per sheet layout (``hop_dist_sheet_maps``) — next season maps itself.
* ✨ ``ai_roles``: one capped AI call suggests roles for columns the rules could not place (user confirms).
* ``row_checks``: the sheet's own % columns (retailer margin, distributor mark-up) are checked against the prices.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

ROLES = ("name_part", "code", "category", "season", "mrp", "purchase_price", "selling_price", "gst_pct", "hsn",
         "unit", "detail", "skip")
ROLE_LABELS = {"name_part": "Part of the name", "code": "Article code", "category": "Category / product",
               "season": "Season", "mrp": "MRP", "purchase_price": "My buying price (DP / Ex-Mill)",
               "selling_price": "Price to retailer (PTR)", "gst_pct": "GST %", "hsn": "HSN", "unit": "Unit",
               "detail": "Keep as detail", "skip": "Skip (not for the master)"}

# header words → role (first match wins; checked on the normalised header)
_RULES: list[tuple[str, tuple[str, ...]]] = [
    # facts about the pack / make — details, even when the header says "size" or "colour"
    ("detail", ("bale", "pack", "moq", "pillow", "weight", "gram", "gsm", "ply", "stitch", "units", "colorways",
                "perceived", "consumer", "description", "print style", "option")),
    ("skip", ("qty", "qnty", "quantity", "booking", "delivery", "order", "sl no", "s no", "sr no", "serial",
              "no of design", "total", "amount", "value")),
    ("purchase_price", ("ex mill", "exmill", "ex-mill", "dp", "dealer price", "distributor price", "nlc", "net rate",
                        "billing rate", "cost price", "landing")),
    ("selling_price", ("ptr", "price to retailer", "retailer price", "rlp", "sale rate", "wholesale")),
    ("mrp", ("mrp", "max retail")),
    ("code", ("code", "art no", "article no", "sku", "item code", "style no", "design no")),
    ("gst_pct", ("gst", "tax")),
    ("hsn", ("hsn",)),
    ("season", ("season",)),
    ("category", ("product", "category", "group", "range", "type")),
    ("unit", ("unit", "uom")),
    ("name_part", ("brand", "article", "item name", "name", "quality name", "size", "shade", "colour", "color", "tc",
                   "thread count")),
]
# % columns are details, never prices (Retailer Margin 0.28, AWD Mark up 0.15, Retail Mark down 0.4)
_PCT_WORDS = ("margin", "mark up", "markup", "mark down", "markdown", "md", "mu", "discount", "%")
_NAME_ORDER = ("brand", "article", "item name", "name", "quality name", "tc", "thread count", "size", "shade",
               "colour", "color")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _s(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, datetime):
        return v.date().isoformat()
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = re.sub(r"[₹,\s]|rs\.?", "", str(v).lower())
    try:
        return float(t)
    except ValueError:
        return None


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9%]+", " ", str(s or "").lower()).strip()


def _has(h: str, words: tuple[str, ...]) -> bool:
    hp = f" {h} "
    return any(f" {_norm(w)} " in hp or h == _norm(w) for w in words)


def guess_role(header: str, samples: list[Any]) -> str:
    h = _norm(header)
    if not h:
        texts = [x for x in samples if _s(x) and _num(x) is None]
        return "detail" if texts else "skip"
    nums = [_num(x) for x in samples if _s(x)]
    nums = [n for n in nums if n is not None]
    if _has(h, _PCT_WORDS) or (nums and all(0 < n < 1 for n in nums) and not _has(h, ("mrp", "ptr", "dp"))):
        return "detail"
    if _has(h, ("selling price", "sp")) and not _has(h, ("ptr", "retailer")):
        return "detail"   # shop selling price (often = MRP after an offer) — kept as a detail, PTR is the retailer price
    for role, words in _RULES:
        if _has(h, words):
            if role == "category" and _has(h, ("print", "style")):
                return "detail"
            if role == "name_part" and _has(h, ("color", "colour")) and nums and len(nums) == len([x for x in samples if _s(x)]):
                return "detail"   # "Color: 3" = number of colours, not a shade
            return role
    return "detail"


def _header_score(row: tuple) -> int:
    cells = [_s(c) for c in row if _s(c)]
    if len(cells) < 3:
        return -1
    texts = [c for c in cells if _num(c) is None]
    hits = sum(1 for c in texts if any(_has(_norm(c), w) for _, w in _RULES))
    return len(texts) + 3 * hits if len(texts) >= 0.6 * len(cells) else -1


def layout_sig(headers: list[str]) -> str:
    """Same columns in a later sheet → same signature → the remembered column roles apply."""
    key = "|".join(_norm(h) for h in headers if _norm(h))
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _load(data: bytes, sheet: str | None):
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    names = [w.title for w in wb.worksheets]
    best, best_n = None, -1
    for w in wb.worksheets:
        if sheet and w.title != sheet:
            continue
        n = (w.max_row or 0) * (w.max_column or 0)
        if sheet or n > best_n:
            best, best_n = w, n
    if best is None:
        raise ValueError("Sheet not found")
    return names, best.title, [tuple(r) for r in best.iter_rows(values_only=True)]


def analyze(data: bytes, sheet: str | None = None) -> dict[str, Any]:
    """Every column of the sheet: header, 3 samples, guessed role. Raw rows kept for ``build``."""
    names, title, rows = _load(data, sheet)
    if not rows:
        raise ValueError("The sheet is empty")
    hdr_i = max(range(min(len(rows), 30)), key=lambda i: _header_score(rows[i]))
    if _header_score(rows[hdr_i]) < 0:
        raise ValueError("Could not find the header row — the sheet needs column names like Article, MRP, DP")
    header = rows[hdr_i]
    width = max(len(r) for r in rows[hdr_i:hdr_i + 300])
    body = [r for r in rows[hdr_i + 1:hdr_i + 2001] if any(_s(c) for c in r)]
    cols = []
    for j in range(width):
        h = _s(header[j]) if j < len(header) else ""
        samples = [r[j] for r in body if j < len(r) and _s(r[j])][:3]
        if not h and not samples:
            continue
        cols.append({"i": j, "header": h or f"Column {chr(65 + j) if j < 26 else j + 1}",
                     "samples": [_s(x)[:40] for x in samples], "role": guess_role(h, samples)})
    return {"sheets": names, "sheet": title, "header_row": hdr_i + 1, "columns": cols,
            "sig": layout_sig([c["header"] for c in cols]), "rows": [list(r) for r in body]}


def _name_from(parts: list[tuple[str, Any]]) -> str:
    out = []
    for h, v in parts:
        t = _s(v)
        if not t or t in ("-", "—"):
            continue
        hn = _norm(h)
        if _num(t) is not None and len(hn) <= 3 and hn and hn not in t.lower():   # 120 + "TC" → "120 TC"
            t = f"{t} {h.strip().upper()}"
        have = set(_norm(" ".join(out)).split())
        if not set(_norm(t).split()) <= have:      # word-level: size "L" is kept even inside "Flora"
            out.append(t)
    return " ".join(out)[:160]


def build(analysis: dict[str, Any], roles: dict[str, str] | None = None, season: str = "") -> list[dict[str, Any]]:
    """Rows → articles: {code, name, category, season, mrp, purchase_price, selling_price, gst_pct, hsn, unit, details}."""
    cols = analysis["columns"]
    role = {int(c["i"]): (roles or {}).get(str(c["i"]), c["role"]) for c in cols}
    hdr = {int(c["i"]): c["header"] for c in cols}
    order = sorted([j for j, r in role.items() if r == "name_part"],
                   key=lambda j: next((k for k, w in enumerate(_NAME_ORDER) if _has(_norm(hdr[j]), (w,))), 99) * 100 + j)
    # Blank cells are copied from the row above only for "group" columns (merged brand / product / unnamed group
    # column) — never for shade, size or prices, where blank really means blank.
    fill_cols = {j for j, r in role.items() if r == "category" or (r == "detail" and hdr[j].startswith("Column "))}
    if order:
        fill_cols.add(order[0])
    prev: dict[int, Any] = {}
    section = ""
    out = []
    for r in analysis["rows"]:
        filled = {j: (r[j] if j < len(r) else None) for j in role}
        non_empty = [j for j, v in filled.items() if _s(v)]
        nums = [j for j in non_empty if _num(filled[j]) is not None]
        if len(non_empty) == 1 and not nums:          # "Fleece Collection" → a section, not an article
            section = _s(filled[non_empty[0]])[:60]
            prev = {}
            continue
        if not any(role[j] in ("mrp", "purchase_price", "selling_price") and _num(filled[j]) for j in role):
            continue                                   # notes / totals rows
        for j, v in filled.items():                    # blank under a merged / repeated cell → the value above
            if not _s(v) and j in fill_cols and j in prev and _num(prev[j]) is None:
                filled[j] = prev[j]
        prev = {j: v for j, v in filled.items() if _s(v)}
        a: dict[str, Any] = {"details": {}}
        for j, v in filled.items():
            rj = role[j]
            if rj in ("mrp", "purchase_price", "selling_price", "gst_pct"):
                n = _num(v)
                if n is not None:
                    a[rj] = round(n * 100, 2) if rj == "gst_pct" and 0 < n < 1 else round(n, 2)
            elif rj in ("code", "category", "season", "hsn", "unit") and _s(v):
                a[rj] = _s(v)[:80]
            elif rj == "detail" and _s(v) and _s(v) not in ("-", "—"):
                n = _num(v)
                a["details"][hdr[j][:40]] = (f"{round(n * 100, 1):g}%" if n is not None and 0 < n < 1 else _s(v)[:80])
        a["name"] = _name_from([(hdr[j], filled[j]) for j in order]) or a.get("code") or ""
        if not a["name"]:
            continue
        if section:
            a["details"]["Collection"] = section
        if season and not a.get("season"):
            a["season"] = season
        a["checks"] = row_checks(a)
        out.append(a)
    _unique_names(out)
    return out


def _unique_names(arts: list[dict[str, Any]]) -> None:
    """Two rows with the same built name: add the first detail that tells them apart, else a number."""
    from collections import defaultdict

    groups: dict[str, list[dict]] = defaultdict(list)
    for a in arts:
        groups[_norm(a["name"])].append(a)
    for same in groups.values():
        if len(same) < 2:
            continue
        keys = [k for k in same[0]["details"] if len({str(x["details"].get(k)) for x in same}) == len(same)]
        for n, a in enumerate(same, 1):
            extra = _s(a["details"].get(keys[0])) if keys else ""
            a["name"] = (f'{a["name"]} · {extra}' if extra else f'{a["name"]} #{n}')[:160]


def row_checks(a: dict[str, Any]) -> list[str]:
    """✨ Checks a row against itself: prices in the right order, and the sheet's own % columns."""
    mrp, dp, ptr = a.get("mrp") or 0, a.get("purchase_price") or 0, a.get("selling_price") or 0
    out = []
    if ptr and mrp and ptr > mrp:
        out.append("Price to retailer is above MRP")
    if dp and ptr and dp > ptr:
        out.append("DP is above the price to retailer — you lose on every piece")
    if dp and mrp and dp > mrp:
        out.append("DP is above MRP")
    pct = {_norm(k): v for k, v in (a.get("details") or {}).items() if str(v).endswith("%")}

    def p(*words):
        for k, v in pct.items():
            if _has(k, words):
                try:
                    return float(str(v).rstrip("%")) / 100
                except ValueError:
                    return None
        return None
    gst = (1.0, 1.05, 1.12, 1.18)   # sheets give PTR / DP with or without GST

    def near(a: float, b: float) -> bool:
        return abs(a - b) <= max(2.0, 0.03 * b)
    md = p("retailer margin", "retail mark down", "retailer md", "retail md")
    shop = p("perceived")
    base = mrp * (1 - shop) if shop and mrp else mrp
    if md and base and ptr and not any(near(base * (1 - md) / g, ptr) for g in gst):
        out.append(f"PTR ₹{ptr:g} does not match MRP − {md * 100:g}% (₹{base * (1 - md):.0f}, with or without GST)")
    mu = p("awd mark up", "awd mu", "awd md", "distributor margin", "mark up")
    if mu and dp and ptr and not any(near(dp * (1 + mu) * g, ptr) or near(ptr * (1 - mu) / g, dp) for g in gst):
        out.append(f"DP ₹{dp:g} does not match the {mu * 100:g}% mark-up on PTR ₹{ptr:g}")
    return out


# ── remembered column roles per sheet layout ────────────────────────────────────────────────────────────
def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS hop_dist_sheet_maps (
        workspace_id TEXT NOT NULL, sig TEXT NOT NULL, roles_json TEXT NOT NULL, headers TEXT,
        updated_at TEXT NOT NULL, PRIMARY KEY (workspace_id, sig))""")


def saved_roles(conn: sqlite3.Connection, ws: str, sig: str) -> dict[str, str] | None:
    ensure_schema(conn)
    r = conn.execute("SELECT roles_json FROM hop_dist_sheet_maps WHERE workspace_id=? AND sig=?", (ws, sig)).fetchone()
    return json.loads(r[0]) if r else None


def remember_roles(conn: sqlite3.Connection, ws: str, sig: str, roles: dict[str, str], headers: list[str]) -> None:
    ensure_schema(conn)
    clean = {str(k): v for k, v in (roles or {}).items() if v in ROLES}
    conn.execute("INSERT OR REPLACE INTO hop_dist_sheet_maps (workspace_id, sig, roles_json, headers, updated_at) "
                 "VALUES (?,?,?,?,?)", (ws, sig, json.dumps(clean), " | ".join(headers)[:1000], _now()))
    conn.commit()


def ai_roles(columns: list[dict[str, Any]], *, user_id: int) -> dict[str, str]:
    """✨ One capped AI call: role for every column from its header + samples. The user confirms on screen."""
    from app.services import hop_ai

    lines = "\n".join(f'{c["i"]}: "{c["header"]}" e.g. {", ".join(c.get("samples") or [])}' for c in columns[:40])
    prompt = ("These are the columns of an Indian textile company's price list / booking sheet for its distributor.\n"
              f"Give each column ONE role from: {', '.join(ROLES)}.\n"
              "name_part = words that together name the article (brand, quality, TC, size, shade). purchase_price = "
              "what the distributor pays (DP, Ex-Mill, NLC). selling_price = price to retailer (PTR). Percent columns "
              "(margin, mark-up, mark-down) and other facts (ply, weight, bale pack, MOQ, stitching) = detail. "
              "Order quantities, delivery months, serial numbers = skip.\n"
              f"Columns:\n{lines}\n"
              'Answer JSON only: {"roles": {"<column number>": "<role>"}}')
    out = hop_ai.ask_json(prompt, user_id=user_id, feature="dist_sheet_columns", max_tokens=300)
    got = out.get("roles") if isinstance(out, dict) else None
    return {str(k): v for k, v in (got or {}).items() if v in ROLES}

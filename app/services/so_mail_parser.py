"""Parse Bombay Dyeing "RFA … orders … for Cash payment" Sales Order mails.

The mail body carries a table of SO numbers with Qty / Basic / Gross and
(depending on the template) Cash Discount %, CD amount, TDS 194Q and Total,
plus a payment due date. Two templates are seen in practice:

* Bedsheet: a party summary row (ORD QTY … CD% … TDS … Total) and a second
  table listing each SO with Qty / Basic / Gross only.
* Towel: one table where every SO row has CD% / CD / TDS / Total and a
  "<party> Total" row at the end.

Both are parsed from the HTML table cells (plain text wraps rows
unpredictably). Numbers use Indian grouping ("62,83,414.68").
"""

from __future__ import annotations

import datetime as _dt
import re
from html.parser import HTMLParser
from typing import Any

_SO_RE = re.compile(r"^\d{9,10}$")
_NUM_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
_PCT_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*%$")

CATEGORY_HINTS = (
    ("BEDSHEET", "Bed"),
    ("BED SHEET", "Bed"),
    ("TOWEL", "Bath"),
    ("BATH", "Bath"),
    ("PILLOW", "Pillow"),
    ("TOB", "TOB"),
    ("BLANKET", "TOB"),
    ("COMFORTER", "TOB"),
    ("QUILT", "TOB"),
)


class _TableCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._stack: list[list[list[str]]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._stack and self._row is not None:
            if any(c for c in self._row):
                self._stack[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def html_tables(html: str) -> list[list[list[str]]]:
    p = _TableCollector()
    p.feed(html or "")
    p.close()
    return p.tables


def html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html or "")
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    from html import unescape

    return re.sub(r"[ \t\xa0]+", " ", unescape(text))


def money(raw: str) -> float | None:
    t = (raw or "").replace("\xa0", "").replace("₹", "").strip()
    if not _NUM_RE.match(t):
        return None
    try:
        return float(t.replace(",", ""))
    except ValueError:
        return None


def category_from_subject(subject: str) -> str | None:
    up = (subject or "").upper()
    for key, cat in CATEGORY_HINTS:
        if key in up:
            return cat
    return None


_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


def due_date_from_text(text: str) -> str | None:
    t = re.sub(r"\s+", " ", text or "")
    m = re.search(
        r"on\s+or\s+before\s+(?:[A-Za-z]+,\s*)?([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", t, re.I
    )
    if m and m.group(1).lower() in _MONTHS:
        try:
            return _dt.date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2))).isoformat()
        except ValueError:
            return None
    m = re.search(r"on\s+or\s+before\s+(\d{1,2})[./-](\d{1,2})[./-](\d{4})", t, re.I)
    if m:
        try:
            return _dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None


def _tds_rate(tables: list[list[list[str]]], text: str) -> float | None:
    blob = " ".join(" ".join(r) for t in tables for r in t) + " " + (text or "")
    m = re.search(r"TDS\s*194Q\s*@\s*(\d+(?:\.\d+)?)\s*%", blob, re.I)
    return float(m.group(1)) if m else None


def _row_values(cells: list[str]) -> dict[str, Any]:
    """Classify one table row: label, SO no., Qty/Basic/Gross, CD%, CD, TDS, Total."""
    cells = [c.strip() for c in cells if c and c.strip()]
    out: dict[str, Any] = {"label": "", "so_number": None, "numbers": [], "cd_percent": None,
                           "tds_dash": False}
    label_parts: list[str] = []
    after_pct: list[Any] = []
    seen_pct = False
    for c in cells:
        if out["so_number"] is None and _SO_RE.match(c):
            out["so_number"] = c
            continue
        pm = _PCT_RE.match(c)
        if pm:
            out["cd_percent"] = float(pm.group(1))
            seen_pct = True
            continue
        if c in ("-", "–", "—"):
            if seen_pct:
                after_pct.append(None)  # TDS shown as "-" (not deducted)
                out["tds_dash"] = True
            continue
        val = money(c)
        if val is not None:
            (after_pct if seen_pct else out["numbers"]).append(val)
            continue
        if not out["numbers"] and out["so_number"] is None:
            label_parts.append(c)
    out["label"] = " ".join(label_parts).strip()
    out["after_pct"] = after_pct
    return out


def _fill(rec: dict[str, Any], r: dict[str, Any]) -> None:
    nums = r["numbers"]
    if len(nums) >= 3:
        rec["qty"], rec["basic"], rec["gross"] = nums[0], nums[1], nums[2]
    if r["cd_percent"] is not None:
        rec["cd_percent"] = r["cd_percent"]
    ap = r["after_pct"]
    # After CD%: CD amount, TDS (number or "-"), Total.
    if len(ap) >= 1:
        rec["cd_amount"] = ap[0]
    if len(ap) >= 3:
        rec["tds_amount"] = ap[1]
        rec["total"] = ap[2]
    elif len(ap) == 2:
        rec["total"] = ap[1]
    if r["tds_dash"]:
        rec["tds_amount"] = None
        rec["tds_dash"] = True


def parse_so_mail(subject: str, html: str, plain: str = "") -> dict[str, Any]:
    tables = html_tables(html) if html else []
    text = html_to_text(html) if html else (plain or "")
    tds_rate = _tds_rate(tables, text + " " + (plain or ""))
    result: dict[str, Any] = {
        "category": category_from_subject(subject),
        "due_date": due_date_from_text(text) or due_date_from_text(plain),
        "tds_rate": tds_rate,
        "so_rows": [],
        "party_totals": [],
        "summary": None,
    }
    seen_so: set[str] = set()
    has_tds_column = False
    for table in tables:
        flat = " ".join(" ".join(r) for r in table).lower()
        if "basic" not in flat or "gross" not in flat:
            continue
        if "tds" in flat:
            has_tds_column = True
        current_label = ""
        for cells in table:
            joined = " ".join(cells).lower()
            if "basic" in joined and "gross" in joined:
                continue  # header row
            r = _row_values(cells)
            if r["label"] and r["so_number"]:
                current_label = r["label"]
            is_total = "total" in r["label"].lower()
            if r["so_number"] and not is_total:
                if r["so_number"] in seen_so:
                    continue
                seen_so.add(r["so_number"])
                rec: dict[str, Any] = {"so_number": r["so_number"], "party": r["label"] or current_label}
                _fill(rec, r)
                result["so_rows"].append(rec)
            elif (is_total or (r["cd_percent"] is not None and not r["so_number"])) and len(r["numbers"]) >= 3:
                rec = {"party": re.sub(r"(?i)\btotal\b", "", r["label"]).strip() or current_label}
                _fill(rec, r)
                # Towel template: the Total row has no CD% cell, so CD [/ TDS] / Total
                # follow Gross directly (5 numbers without TDS, 6 with).
                if r["cd_percent"] is None:
                    extra = r["numbers"][3:]
                    if len(extra) >= 3:
                        rec["cd_amount"], rec["tds_amount"], rec["total"] = extra[:3]
                    elif len(extra) == 2:
                        rec["cd_amount"], rec["total"] = extra
                _merge_party_total(result["party_totals"], rec)

    so_rows = result["so_rows"]
    totals = result["party_totals"]
    if totals:
        summary: dict[str, Any] = {"party": " + ".join(t.get("party") or "" for t in totals).strip(" +")}
        for key in ("qty", "basic", "gross", "cd_amount", "tds_amount", "total"):
            vals = [t.get(key) for t in totals if t.get(key) is not None]
            summary[key] = round(sum(vals), 2) if vals else None
        pcts = {t.get("cd_percent") for t in totals if t.get("cd_percent") is not None}
        pcts |= {r.get("cd_percent") for r in so_rows if r.get("cd_percent") is not None}
        summary["cd_percent"] = pcts.pop() if len(pcts) == 1 else None
        summary["tds_dash"] = any(t.get("tds_dash") for t in totals)
        result["summary"] = summary
    elif so_rows:
        summary = {"party": so_rows[0].get("party")}
        for key in ("qty", "basic", "gross"):
            summary[key] = round(sum(r.get(key) or 0 for r in so_rows), 2)
        result["summary"] = summary
    s = result["summary"]
    if s is not None:
        # TDS 194Q applies when the mail shows an amount; "-" or no TDS
        # column at all means it was not deducted.
        s["tds_applied"] = bool(s.get("tds_amount")) and not s.get("tds_dash", False) and has_tds_column
    return result


def _merge_party_total(totals: list[dict[str, Any]], rec: dict[str, Any]) -> None:
    """Same party/table reported twice (Bedsheet summary row + "<party> Total"
    row) is one total: merge when Qty and Basic agree, else keep both
    (e.g. a mail carrying separate SPL and regular tables)."""
    for t in totals:
        if t.get("qty") == rec.get("qty") and t.get("basic") == rec.get("basic"):
            for k, v in rec.items():
                if v is not None and t.get(k) is None:
                    t[k] = v
            if rec.get("tds_dash"):
                t["tds_dash"] = True
            return
    totals.append(rec)

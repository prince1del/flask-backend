"""Voice bill (owner QnA 21-11, step 3): "Sharma Traders, 10 Aster double bedsheet 719 ke rate se, 5 Flora towel".

1. The phone turns speech into text (on the phone, free); the user can fix the text first.
2. One capped AI call only SPLITS the text: party, and per line item / qty / rate / unit. It never picks or
   invents a party or an item.
3. Party and items are matched here, ONLY against this UI's saved parties and items (word match). A clear winner
   is filled in; otherwise the line is marked unsure with up to 3 options to tap.
4. A rate that was not spoken comes from the item (price to retailer / selling price, or the buying price on a
   purchase), wholesale when the qty reaches its minimum.

The answer has the same shape as ✨ Fill with AI (``hop_ai.match_sale``), so the phone fills the bill form the
same way. Nothing is saved — the user checks and taps Save.
"""
from __future__ import annotations

import re
import sqlite3
from difflib import SequenceMatcher
from typing import Any

from app import hop_items

_PROMPT = """Split a shopkeeper's spoken bill (English / Hindi / Hinglish, speech-to-text, may have mistakes)
into JSON only: {"party": "", "lines": [{"item": "", "qty": 1, "rate": 0, "unit": ""}]}
- party: the customer / supplier name said (usually first). Empty if none.
- item: the item words as said (keep size / design words like "double", "king", "90x100"). Never add words.
- qty: number of pieces (hindi numbers: ek 1, do 2, teen 3, char 4, paanch 5, chhe 6, saat 7, aath 8, nau 9,
  das 10, bees 20, pachaas 50, sau 100). Default 1.
- rate: only if a price per piece was said ("719 ke rate se", "@ 719", "719 wala"); else 0.
- unit: only if said (box, dozen, set, mtr); else "".
Text: """

_STOP = {"ka", "ki", "ke", "aur", "and", "the", "of", "wala", "wali", "wale", "piece", "pieces", "pcs", "pc", "nos"}


def _words(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", str(s or "").lower()) if w not in _STOP]


def score(spoken: str, name: str, code: str = "") -> float:
    """0..1 how well the spoken words fit an item / party name (every spoken word should be in it)."""
    sw, nw = _words(spoken), _words(name) + _words(code)
    if not sw or not nw:
        return 0.0
    hit = 0.0
    for w in sw:
        best = 0.0
        for n in nw:
            if w == n:
                best = 1.0
                break
            if len(w) >= 3 and (n.startswith(w) or w.startswith(n) and len(n) >= 3):
                best = max(best, 0.9)
            elif len(w) >= 4:                       # speech-to-text spelling slips ("flowra" → "flora")
                r = SequenceMatcher(None, w, n).ratio()
                if r >= 0.8:
                    best = max(best, r)
        hit += best
    cover = hit / len(sw)
    extra = len(nw) - len(sw)          # a shorter name that holds every word beats a long one
    return round(cover - max(0, extra) * 0.02, 3)


def _rank(spoken: str, rows: list[dict], name_key: str, code_key: str | None = None) -> list[tuple[float, dict]]:
    out = [(score(spoken, r.get(name_key) or "", (r.get(code_key) or "") if code_key else ""), r) for r in rows]
    out = [x for x in out if x[0] >= 0.5]
    out.sort(key=lambda x: -x[0])
    return out[:3]


def _sure(ranked: list[tuple[float, dict]]) -> bool:
    return bool(ranked) and ranked[0][0] >= 0.85 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.1)


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").replace("₹", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _line_from(prod: dict, qty: float, rate: float, party_type: str) -> dict[str, Any]:
    rate_from = "spoken"
    if not rate:
        r2, src = hop_items.price_for_qty(prod, qty, party_type)
        rate, rate_from = float(r2 or 0), (src or "missing")
    return {"product_id": prod["id"], "item_name": prod["name"], "rate": rate, "rate_from": rate_from if rate else "missing",
            "tax_pct": float(prod.get("gst_pct") or 0), "hsn": str(prod.get("hsn") or "")[:12],
            "unit": (str(prod.get("unit") or "PCS")[:12] or "PCS").upper()}


def draft(conn: sqlite3.Connection, ws: str, text: str, *, user_id: int, kind: str = "sale",
          parsed: dict[str, Any] | None = None) -> dict[str, Any]:
    """Spoken text → bill draft (``parsed`` lets tests skip the AI call)."""
    text = str(text or "").strip()[:600]
    if not text:
        raise ValueError("Say the party and the items first")
    if parsed is None:
        from app.services import hop_ai

        parsed = hop_ai.ask_json(_PROMPT + text, user_id=user_id, feature="nexora_agent_hop_voice_bill", max_tokens=350)
    purchase = kind == "purchase"
    party_type = "vendor" if purchase else "customer"
    table = "hop_vendors" if purchase else "hop_customers"
    parties = [dict(r) for r in conn.execute(f"SELECT id, company, mobile FROM {table} WHERE workspace_id=?", (ws,))]
    products = [dict(r) for r in conn.execute(
        "SELECT * FROM hop_products WHERE workspace_id=? AND (deleted_at IS NULL OR trim(deleted_at)='') "
        "AND COALESCE(status,'active')!='inactive'", (ws,))]
    party_said = str(parsed.get("party") or "").strip()[:120]
    ranked_p = _rank(party_said, parties, "company") if party_said else []
    party = ranked_p[0][1] if _sure(ranked_p) else None
    checks: list[str] = []
    lines = []
    for ln in (parsed.get("lines") or [])[:30]:
        if not isinstance(ln, dict):
            continue
        said = str(ln.get("item") or "").strip()[:120]
        if not said:
            continue
        qty = _num(ln.get("qty")) or 1.0
        rate = _num(ln.get("rate"))
        ranked = _rank(said, products, "name", "code")
        options = [{**_line_from(p, qty, rate, party_type), "score": round(sc * 100)} for sc, p in ranked]
        sure = _sure(ranked)
        if ranked:
            base = dict(options[0])
            base.pop("score", None)
        else:
            base = {"product_id": None, "item_name": said, "rate": rate, "rate_from": "spoken" if rate else "missing",
                    "tax_pct": 0.0, "hsn": "", "unit": "PCS"}
        unit = str(ln.get("unit") or "").strip().upper()[:12]
        lines.append({**base, "qty": qty, "unit": unit or base["unit"], "said": said, "matched_item": sure,
                      "unsure": not sure, "options": options if not sure else [], "discount_pct": 0.0, "description": ""})
        if not ranked:
            checks.append(f"\"{said}\" is not in your items — add it, or pick the right item.")
        elif not sure:
            checks.append(f"\"{said}\": which one? " + " / ".join(o["item_name"] for o in options))
    if party_said and not party:
        checks.insert(0, f"\"{party_said}\": " + ("which one? " + " / ".join(p["company"] for _, p in ranked_p)
                                                  if ranked_p else "not in your parties — add it, then pick it."))
    if not party_said:
        checks.insert(0, "No party said — pick the " + ("supplier." if purchase else "customer."))
    missing = [l["item_name"] for l in lines if l["rate_from"] == "missing"]
    if missing:
        checks.append("No rate for: " + ", ".join(missing[:6]) + " — type it.")
    total = round(sum(l["qty"] * l["rate"] * (1 + l["tax_pct"] / 100) for l in lines), 2)
    who = (party or {}).get("company") or party_said or "No party"
    n = len(lines)
    say = f"{who}, {n} item{'s' if n != 1 else ''}, total ₹{total:,.0f}. Correct?"
    key = "vendor_id" if purchase else "customer_id"
    return {"kind": kind, key: party["id"] if party else None,
            ("vendor_name" if purchase else "customer_match"): party["company"] if party else None,
            "customer_name": party_said, "party_options": [{"id": p["id"], "name": p["company"], "score": round(sc * 100)}
                                                         for sc, p in ranked_p] if party_said and not party else [],
            "lines": lines, "items_total": total, "total": total, "text": text, "say": say, "checks": checks,
            "date": "", "notes": ""}

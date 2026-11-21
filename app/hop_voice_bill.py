"""Voice bill (owner QnA 21-11, step 3): "Sharma Traders, 10 Aster double bedsheet 719 ke rate se, 5 Flora towel".

1. The phone turns speech into text (on the phone, free); the user can fix the text first.
2. One capped AI call splits the text (party, item / qty / rate / unit) and may point to a name from a short list
   of THIS UI's saved items / parties that sound like the words (the phone's other guesses are sent too), so unclear
   speech still lands on the right product. A name not in the list is ignored.
3. Party and items are matched here (``hop_name_match``: words, sounds, learned words) ONLY against the saved
   lists. A clear winner is filled in; otherwise the line is unsure with up to 3 options to tap — the pick is
   learned (``/voice-learn``) so the same words go straight to it next time.
4. A rate that was not spoken comes from the item (price to retailer / selling price, or the buying price on a
   purchase), wholesale when the qty reaches its minimum.

The answer has the same shape as ✨ Fill with AI (``hop_ai.match_sale``), so the phone fills the bill form the
same way. Nothing is saved — the user checks and taps Save.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from app import hop_items, hop_name_match as NM

_PROMPT = """Split a shopkeeper's spoken bill (English / Hindi / Hinglish, speech-to-text, may be unclear or
misheard) into JSON only:
{"party": "", "party_pick": "", "lines": [{"item": "", "pick": "", "qty": 1, "rate": 0, "unit": ""}]}
- party: the customer / supplier name as said (usually first). Empty if none.
- party_pick: the EXACT name from PARTIES that was meant (sounds alike counts), else "".
- item: the item words as said (keep size / design words like "double", "king", "90x100").
- pick: the EXACT name from ITEMS that was meant (sounds alike / misheard counts: "aaster dabal" = "Aster Double
  Bedsheet"), else "". Never a name that is not in ITEMS.
- qty: pieces (hindi numbers: ek 1, do 2, teen 3, char 4, paanch 5, chhe 6, saat 7, aath 8, nau 9, das 10,
  bees 20, pachaas 50, sau 100). Default 1.
- rate: only if a price per piece was said ("719 ke rate se", "@ 719", "719 wala"); else 0.
- unit: only if said (box, dozen, set, mtr); else "".
"""

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
          alternatives: list[str] | None = None, parsed: dict[str, Any] | None = None) -> dict[str, Any]:
    """Spoken text (+ the phone's other guesses of the same speech) → bill draft. ``parsed`` lets tests skip AI."""
    text = str(text or "").strip()[:600]
    if not text:
        raise ValueError("Say the party and the items first")
    alts = [str(a).strip()[:300] for a in (alternatives or []) if str(a or "").strip() and str(a).strip() != text][:4]
    purchase = kind == "purchase"
    party_type = "vendor" if purchase else "customer"
    parties = NM._parties(conn, ws, party_type)
    products = NM._products(conn, ws)
    if parsed is None:
        from app.services import hop_ai

        items_short = NM.shortlist([text, *alts], products, "name", "code", limit=40)
        parties_short = NM.shortlist([text, *alts], parties, "company", limit=15)
        prompt = "\n".join([
            _PROMPT + "ITEMS: " + "; ".join(p["name"] for p in items_short),
            "PARTIES: " + "; ".join(p["company"] for p in parties_short),
            "HEARD: " + text,
            ("OTHER GUESSES OF THE SAME SPEECH: " + " | ".join(alts)) if alts else "",
        ])
        parsed = hop_ai.ask_json(prompt, user_id=user_id, feature="nexora_agent_hop_voice_bill", max_tokens=400)
    by_name = {str(p.get("name") or "").strip().lower(): p for p in products}
    party_by_name = {str(p.get("company") or "").strip().lower(): p for p in parties}

    party_said = str(parsed.get("party") or "").strip()[:120]
    pf = NM.find(conn, ws, party_type, party_said, parties) if party_said else {"best": None, "options": [], "learned": False}
    party, ranked_p = pf["best"], pf["options"]
    ai_party = party_by_name.get(str(parsed.get("party_pick") or "").strip().lower())
    if party is None and ai_party is not None:
        # AI heard it as a saved party: sure when our words agree a little, else the first option to tap
        if NM.score(party_said, ai_party["company"]) >= 0.5 or not ranked_p:
            party = ai_party
        else:
            ranked_p = [(1.0, ai_party)] + [x for x in ranked_p if x[1]["id"] != ai_party["id"]][:2]
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
        f = NM.find(conn, ws, "item", said, products)
        ranked, best = f["options"], f["best"]
        ai_pick = by_name.get(str(ln.get("pick") or "").strip().lower())
        if best is None and ai_pick is not None:
            if NM.score(said, ai_pick["name"], ai_pick.get("code") or "") >= 0.5 or not ranked:
                best = ai_pick
            ranked = [(1.0, ai_pick)] + [x for x in ranked if x[1]["id"] != ai_pick["id"]][:2]
        options = [{**_line_from(p, qty, rate, party_type), "score": round(sc * 100)} for sc, p in ranked]
        top = best or (ranked[0][1] if ranked else None)
        if top is not None:
            base = _line_from(top, qty, rate, party_type)
        else:
            base = {"product_id": None, "item_name": said, "rate": rate, "rate_from": "spoken" if rate else "missing",
                    "tax_pct": 0.0, "hsn": "", "unit": "PCS"}
        unit = str(ln.get("unit") or "").strip().upper()[:12]
        is_sure = best is not None
        lines.append({**base, "qty": qty, "unit": unit or base["unit"], "said": said, "matched_item": is_sure,
                      "learned": f["learned"], "unsure": not is_sure, "options": options if not is_sure else [],
                      "discount_pct": 0.0, "description": ""})
        if top is None:
            checks.append(f"\"{said}\" is not in your items — add it, or pick the right item.")
        elif not is_sure:
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
            "customer_name": party_said, "party_said": party_said, "party_kind": party_type,
            "party_options": [{"id": p["id"], "name": p["company"], "score": round(sc * 100)}
                              for sc, p in ranked_p] if party_said and not party else [],
            "lines": lines, "items_total": total, "total": total, "text": text, "say": say, "checks": checks,
            "date": "", "notes": ""}

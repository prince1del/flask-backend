"""GST % from an HSN code (rule based, no AI cost) — used when a product is made from a photo.

Only well-known rates are listed (textiles / home furnishing / apparel / footwear, the trade NEXORA serves first).
Rates after the GST rate change of 22-09-2025: textile made-ups, apparel and footwear are 5% up to ₹2,500 a piece
and 18% above. Anything not listed returns None — then the category's default GST is used, or the user types it.
The result is always a suggestion: the user sees it on the form and saves.
"""
from __future__ import annotations

from typing import Any

# (HSN prefix, low %, high %, piece price line or None, what it is)
_RULES: tuple[tuple[str, float, float, float | None, str], ...] = (
    ("5701", 5, 5, None, "carpets"), ("5702", 5, 5, None, "carpets"), ("5703", 5, 5, None, "carpets"),
    ("5704", 5, 5, None, "carpets"), ("5705", 5, 5, None, "carpets / mats"),
    ("6301", 5, 18, 2500, "blankets"),
    ("6302", 5, 18, 2500, "bed / table / toilet linen (bedsheets, towels)"),
    ("6303", 5, 18, 2500, "curtains"),
    ("6304", 5, 18, 2500, "furnishing articles (bed covers, cushion covers)"),
    ("6305", 5, 18, 2500, "sacks / bags"),
    ("6306", 5, 18, 2500, "tarpaulins / tents"),
    ("6307", 5, 18, 2500, "other made-ups"),
    ("6308", 5, 18, 2500, "sets"),
    ("61", 5, 18, 2500, "knitted apparel"),
    ("62", 5, 18, 2500, "apparel (not knitted)"),
    ("64", 5, 18, 2500, "footwear"),
    ("50", 5, 5, None, "silk / fabric"), ("52", 5, 5, None, "cotton yarn / fabric"),
    ("54", 5, 5, None, "man-made filament fabric"), ("55", 5, 5, None, "man-made staple fabric"),
)


def _digits(hsn: Any) -> str:
    return "".join(ch for ch in str(hsn or "") if ch.isdigit())


def suggest(hsn: Any, piece_price: Any = None) -> dict[str, Any] | None:
    """{"gst_pct", "why"} for this HSN (and selling price per piece, for the ₹2,500 line), or None if not known."""
    h = _digits(hsn)
    if len(h) < 2:
        return None
    for prefix, low, high, line, what in sorted(_RULES, key=lambda r: -len(r[0])):
        if not h.startswith(prefix):
            continue
        if line is None or low == high:
            return {"gst_pct": float(low), "why": f"HSN {prefix} {what}: {low:g}%"}
        try:
            price = float(piece_price or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            return {"gst_pct": float(low), "why": f"HSN {prefix} {what}: {low:g}% up to ₹{line:,.0f} a piece, "
                                                  f"{high:g}% above — no price yet, check it"}
        pct = low if price <= line else high
        return {"gst_pct": float(pct), "why": f"HSN {prefix} {what}: ₹{price:,.0f} a piece is "
                                              f"{'up to' if price <= line else 'above'} ₹{line:,.0f} → {pct:g}%"}
    return None

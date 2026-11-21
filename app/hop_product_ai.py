"""Photo → new product (owner QnA 21-11, step 2): one photo fills the item form.

1. The phone reads the EAN barcode itself (free) and sends it. If that EAN is already in the master, the item is
   returned and NO AI call is made.
2. Otherwise one capped AI call reads the tag: name, brand, MRP, size, design, colour, material, HSN, EAN.
3. GST % comes from the HSN rules (``hop_hsn_gst``), else the category's default GST (GST by category).
4. Items whose photo looks the same are listed, so the same product is not added twice.

Nothing is saved here — the phone opens the form filled in, the user checks it and saves (then the photo is saved
with its fingerprint).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app import hop_hsn_gst, hop_items


def _category_gst(conn: sqlite3.Connection, ws: str, category: str) -> float | None:
    try:
        from app.hop_dist_extra import category_defaults

        defaults = category_defaults(conn, ws)
    except Exception:
        return None
    c = (category or "").strip().lower()
    for k, v in defaults.items():
        if k.strip().lower() == c and v is not None:
            return float(v)
    return None


def read_photo(conn: sqlite3.Connection, db_path: str, ws: str, data: bytes, mime: str, *, user_id: int,
               ean: str = "") -> dict[str, Any]:
    from app import hop_product_photos as PH

    ean = "".join(ch for ch in str(ean or "") if ch.isdigit())
    if ean:
        hit = hop_items.find_by_barcode(conn, ws, ean)
        if hit:
            return {"existing": hit, "fields": None, "gst": None, "similar": [], "ai_used": False,
                    "checks": [f"Barcode {ean} is already {hit.get('name')} — opened it instead (no AI used)."]}
    from app.services import hop_ai

    f = hop_ai.read_product_photo(data, mime or "image/jpeg", user_id=user_id)
    if ean:
        f["ean"] = ean            # the phone's own read is exact; AI's number is only a fallback
    existing = hop_items.find_by_barcode(conn, ws, f["ean"]) if f.get("ean") else None
    gst = hop_hsn_gst.suggest(f.get("hsn"), f.get("mrp"))
    if gst and f.get("mrp"):
        gst["why"] += " (from MRP — check against your selling price)"
    if not gst and f.get("category"):
        cg = _category_gst(conn, ws, f["category"])
        if cg is not None:
            gst = {"gst_pct": cg, "why": f"Default GST of category {f['category']}: {cg:g}%"}
    try:
        similar = PH.match(conn, db_path, ws, data)
    except ValueError:
        similar = []
    checks = []
    if not f.get("name"):
        checks.append("Name not read — type it.")
    if not f.get("mrp"):
        checks.append("MRP not printed / not read — type it.")
    if not f.get("hsn"):
        checks.append("HSN not on the tag — add it for GST bills.")
    if not gst:
        checks.append("GST % not known for this item — type it (or set GST by category).")
    if f.get("confidence") == "low":
        checks.append("Photo was hard to read — check every field.")
    if existing:
        checks.append(f"Barcode {f['ean']} is already {existing.get('name')}.")
    for s in similar:
        if s["score"] >= 80:
            checks.append(f"Looks like {s['name']} (already in the master) — open it instead?")
    return {"fields": f, "gst": gst, "existing": existing, "similar": similar, "checks": checks, "ai_used": True}

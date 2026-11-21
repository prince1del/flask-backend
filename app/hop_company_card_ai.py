"""✨ Photo → company details (owner 21-11: "ai daal rahe ho na har jagah").

One photo (visiting card, letterhead, GST certificate, cancelled cheque) fills a company form — a brand in My brands
(Distributor) or the business's own Edit profile (every UI). One capped AI call inside the daily limit, logged.
What the AI read is then checked for free: GSTIN → state / PAN from the number (and the GST lookup when it works),
IFSC → bank and branch from the IFSC directory. Nothing is saved here — the phone fills the form, the user checks and
saves.
"""
from __future__ import annotations

from typing import Any


def read(data: bytes, mime: str, *, user_id: int) -> dict[str, Any]:
    from app.services import hop_ai
    from app.services.gstin_lookup import lookup_gstin
    from app.services.ifsc_lookup import lookup_ifsc

    f = hop_ai.read_company_card(data, mime or "image/jpeg", user_id=user_id)
    checks: list[str] = []
    if f["gst_no"]:
        try:
            g = lookup_gstin(f["gst_no"]) or {}
        except Exception:
            g = {}
        if g.get("valid_format") is False:
            checks.append(f"GSTIN {f['gst_no']} does not look right — check it.")
            f["gst_no"] = ""
        else:
            if not f["state"] and g.get("state"):
                f["state"] = g["state"]
            if not f["pan"] and g.get("pan"):
                f["pan"] = g["pan"]
            name = g.get("company") or g.get("trade_name") or g.get("legal_name")
            if name and f["company"] and name.lower().split()[0] not in f["company"].lower():
                checks.append(f"GST record name is \"{name}\" — check the company name.")
            if not f["address"] and g.get("address"):
                f["address"] = g["address"]
            if not f["pincode"] and g.get("pincode"):
                f["pincode"] = str(g["pincode"])
    if f["bank_ifsc"]:
        b = lookup_ifsc(f["bank_ifsc"])
        if b.get("ok"):
            f["bank_name"] = f["bank_name"] or b.get("bank_name", "")
            f["bank_branch"] = b.get("branch_with_address", "")
        elif b.get("valid_format") is False:
            checks.append(f"IFSC {f['bank_ifsc']} does not look right — check it.")
    if not f["company"]:
        checks.append("Company name not read — type it.")
    if not f["gst_no"]:
        checks.append("GSTIN not on this photo — add it for GST bills.")
    if f["confidence"] == "low":
        checks.append("Photo was hard to read — check every field.")
    filled = sum(1 for k, v in f.items() if k not in ("doc_type", "confidence") and v)
    return {"fields": f, "filled": filled, "checks": checks, "ai_used": True}

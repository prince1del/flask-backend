"""E-Invoice and E-Way Bill files (government JSON formats) from HoP sale invoices.

No GSP / API account needed: the user downloads the JSON and uploads it on the
e-invoice portal (bulk upload, schema INV-01 v1.1) or the e-way bill portal
(bulk generation tool). After that, the IRN / Ack no / E-Way Bill no can be saved
back on the invoice. Before export, every problem the portal would reject
(missing GSTIN, PIN, state, HSN …) is listed so it can be fixed first.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from typing import Any

STATE_CODES = {
    "jammu and kashmir": "01", "himachal pradesh": "02", "punjab": "03", "chandigarh": "04", "uttarakhand": "05",
    "haryana": "06", "delhi": "07", "rajasthan": "08", "uttar pradesh": "09", "bihar": "10", "sikkim": "11",
    "arunachal pradesh": "12", "nagaland": "13", "manipur": "14", "mizoram": "15", "tripura": "16", "meghalaya": "17",
    "assam": "18", "west bengal": "19", "jharkhand": "20", "odisha": "21", "chhattisgarh": "22", "madhya pradesh": "23",
    "gujarat": "24", "dadra and nagar haveli and daman and diu": "26", "maharashtra": "27", "karnataka": "29",
    "goa": "30", "lakshadweep": "31", "kerala": "32", "tamil nadu": "33", "puducherry": "34",
    "andaman and nicobar islands": "35", "telangana": "36", "andhra pradesh": "37", "ladakh": "38",
}
UQC = {"pcs": "PCS", "pc": "PCS", "piece": "PCS", "nos": "NOS", "no": "NOS", "mtr": "MTR", "m": "MTR", "meter": "MTR",
       "metre": "MTR", "kg": "KGS", "kgs": "KGS", "set": "SET", "sets": "SET", "box": "BOX", "roll": "ROL", "rolls": "ROL",
       "sqft": "SQF", "sq ft": "SQF", "sqm": "SQM", "pair": "PRS", "dozen": "DOZ", "ltr": "LTR", "unit": "UNT", "units": "UNT"}
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _s(v: Any) -> str:
    return str(v or "").strip()


def _r(v: float) -> float:
    return round(v + 0.0, 2)


def _state(gstin: str, state_name: str) -> str:
    if len(gstin) >= 2 and gstin[:2].isdigit():
        return gstin[:2]
    return STATE_CODES.get(_s(state_name).lower(), "")


def _pin(*texts: str) -> str:
    for t in texts:
        m = re.search(r"\b(\d{6})\b", _s(t))
        if m:
            return m.group(1)
    return ""


def _date(d: str) -> str:
    try:
        return datetime.strptime(_s(d)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return ""


def _one(conn: sqlite3.Connection, ws: str, txn_id: int) -> tuple[dict, dict, dict, list[dict], list[str]]:
    conn.row_factory = sqlite3.Row
    t = conn.execute("SELECT * FROM hop_party_transactions WHERE workspace_id=? AND id=? "
                     "AND (deleted_at IS NULL OR trim(deleted_at)='')", (ws, int(txn_id))).fetchone()
    if not t:
        raise LookupError(f"Invoice {txn_id} not found")
    t = dict(t)
    if int(t.get("txn_type") or 0) != 1:
        raise ValueError(f"{t.get('txn_number')}: only Sale Invoices can be exported")
    firm = dict(conn.execute("SELECT * FROM hop_firm_profile WHERE workspace_id=?", (ws,)).fetchone() or {})
    party = dict(conn.execute("SELECT * FROM hop_customers WHERE workspace_id=? AND id=?",
                              (ws, int(t.get("party_id") or 0))).fetchone() or {}) if t.get("party_id") else {}
    lines = [dict(r) for r in conn.execute("SELECT * FROM hop_txn_lines WHERE workspace_id=? AND source_txn_id=? "
                                           "ORDER BY line_no, id", (ws, int(t["source_txn_id"]))).fetchall()]
    no = t.get("txn_number") or f"#{t['id']}"
    problems = []
    fg, pg = _s(firm.get("gstin")).upper(), _s(party.get("gst_no")).upper()
    if not GSTIN_RE.match(fg):
        problems.append("Your GSTIN is missing or wrong (Settings → Company profile).")
    if not _pin(firm.get("pincode"), firm.get("address")):
        problems.append("Your PIN code is missing (Settings → Company profile).")
    if not GSTIN_RE.match(pg):
        problems.append(f"{no}: customer {t.get('party_name')} has no valid GSTIN — e-invoice is only for B2B.")
    if not _pin(party.get("address"), party.get("shipping_address")):
        problems.append(f"{no}: customer {t.get('party_name')} has no 6-digit PIN in the address.")
    if len(_s(no)) > 16:
        problems.append(f"{no}: invoice number is longer than 16 characters (portal limit).")
    for ln in lines:
        if not _s(ln.get("hsn")):
            problems.append(f"{no}: item {ln.get('item_name')} has no HSN.")
    return t, firm, party, lines, problems


def _amounts(t: dict, firm: dict, party: dict, lines: list[dict]):
    fg, pg = _s(firm.get("gstin")).upper(), _s(party.get("gst_no")).upper()
    seller_st = _state(fg, firm.get("state"))
    buyer_st = _state(pg, party.get("state"))
    pos = _s(t.get("place_of_supply")) or buyer_st
    inter = bool(seller_st and pos and seller_st != pos)
    items, tot = [], {"ass": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "disc": 0.0}
    for i, ln in enumerate(lines, 1):
        qty, rate = _f(ln.get("qty")), _f(ln.get("rate"))
        gross = _r(qty * rate)
        disc = _r(_f(ln.get("discount_amount")))
        ass = _r(gross - disc)
        tax = _r(_f(ln.get("tax_amount")))
        cg = sg = 0.0 if inter else _r(tax / 2)
        ig = tax if inter else 0.0
        items.append({"sl": i, "name": _s(ln.get("item_name"))[:300], "hsn": _s(ln.get("hsn")), "qty": qty,
                      "unit": UQC.get(_s(ln.get("unit")).lower(), "OTH"), "rate": rate, "gross": gross, "disc": disc,
                      "ass": ass, "gst_rt": _f(ln.get("tax_pct")), "igst": ig, "cgst": cg, "sgst": sg,
                      "total": _r(ass + tax)})
        tot["ass"] += ass
        tot["cgst"] += cg
        tot["sgst"] += sg
        tot["igst"] += ig
        tot["disc"] += disc
    other = _r(_f(t.get("shipping_amount")) + _f(t.get("shipping_tax_amount")) - _f(t.get("discount_amount")))
    return seller_st, buyer_st, inter, items, {k: _r(v) for k, v in tot.items()}, other


def einvoice_json(conn: sqlite3.Connection, ws: str, txn_ids: list[int]) -> dict[str, Any]:
    out, problems = [], []
    for tid in txn_ids:
        t, firm, party, lines, probs = _one(conn, ws, tid)
        problems += probs
        seller_st, buyer_st, inter, items, tot, other = _amounts(t, firm, party, lines)
        out.append({
            "Version": "1.1",
            "TranDtls": {"TaxSch": "GST", "SupTyp": "B2B", "RegRev": "Y" if t.get("reverse_charge") else "N",
                         "IgstOnIntra": "N"},
            "DocDtls": {"Typ": "INV", "No": _s(t.get("txn_number"))[:16], "Dt": _date(t.get("txn_date"))},
            "SellerDtls": {"Gstin": _s(firm.get("gstin")).upper(), "LglNm": _s(firm.get("firm_name")) or "Seller",
                           "Addr1": (_s(firm.get("address")) or "-")[:100], "Loc": (_s(firm.get("state")) or "-")[:50],
                           "Pin": int(_pin(firm.get("pincode"), firm.get("address")) or 0), "Stcd": seller_st},
            "BuyerDtls": {"Gstin": _s(party.get("gst_no")).upper(), "LglNm": _s(party.get("billing_name") or party.get("company")) or "Buyer",
                          "Pos": _s(t.get("place_of_supply")) or buyer_st, "Addr1": (_s(party.get("address")) or "-")[:100],
                          "Loc": (_s(party.get("city") or party.get("state")) or "-")[:50],
                          "Pin": int(_pin(party.get("address"), party.get("shipping_address")) or 0), "Stcd": buyer_st},
            "ItemList": [{"SlNo": str(i["sl"]), "PrdDesc": i["name"], "IsServc": "N", "HsnCd": i["hsn"], "Qty": i["qty"],
                          "Unit": i["unit"], "UnitPrice": i["rate"], "TotAmt": i["gross"], "Discount": i["disc"],
                          "AssAmt": i["ass"], "GstRt": i["gst_rt"], "IgstAmt": i["igst"], "CgstAmt": i["cgst"],
                          "SgstAmt": i["sgst"], "TotItemVal": i["total"]} for i in items],
            "ValDtls": {"AssVal": tot["ass"], "CgstVal": tot["cgst"], "SgstVal": tot["sgst"], "IgstVal": tot["igst"],
                        "OthChrg": max(other, 0.0), "Discount": max(-other, 0.0),
                        "RndOffAmt": _r(_f(t.get("round_off"))), "TotInvVal": _r(_f(t.get("total_amount")))},
        })
    return {"json": out, "problems": problems, "count": len(out)}


def eway_json(conn: sqlite3.Connection, ws: str, txn_ids: list[int], transport: dict[str, Any] | None = None) -> dict[str, Any]:
    tr = transport or {}
    bills, problems = [], []
    mode = {"road": "1", "rail": "2", "air": "3", "ship": "4"}.get(_s(tr.get("mode")).lower() or "road", "1")
    if mode == "1" and not _s(tr.get("vehicle_no")) and not _s(tr.get("transporter_id")):
        problems.append("Add a vehicle number or transporter ID for road transport.")
    for tid in txn_ids:
        t, firm, party, lines, probs = _one(conn, ws, tid)
        problems += [p for p in probs if "e-invoice is only for B2B" not in p and "16 characters" not in p]
        seller_st, buyer_st, inter, items, tot, _other = _amounts(t, firm, party, lines)
        pg = _s(party.get("gst_no")).upper()
        bills.append({
            "userGstin": _s(firm.get("gstin")).upper(), "supplyType": "O", "subSupplyType": 1, "docType": "INV",
            "docNo": _s(t.get("txn_number"))[:16], "docDate": _date(t.get("txn_date")), "transType": 1,
            "fromGstin": _s(firm.get("gstin")).upper(), "fromTrdName": _s(firm.get("firm_name")),
            "fromAddr1": _s(firm.get("address"))[:120], "fromPlace": _s(firm.get("state"))[:50],
            "fromPincode": int(_pin(firm.get("pincode"), firm.get("address")) or 0), "fromStateCode": int(seller_st or 0),
            "actFromStateCode": int(seller_st or 0),
            "toGstin": pg if GSTIN_RE.match(pg) else "URP", "toTrdName": _s(party.get("billing_name") or party.get("company")),
            "toAddr1": _s(party.get("shipping_address") or party.get("address"))[:120],
            "toPlace": _s(party.get("city") or party.get("state"))[:50],
            "toPincode": int(_pin(party.get("shipping_address"), party.get("address")) or 0),
            "toStateCode": int(buyer_st or 0), "actToStateCode": int(buyer_st or 0),
            "totalValue": tot["ass"], "cgstValue": tot["cgst"], "sgstValue": tot["sgst"], "igstValue": tot["igst"],
            "cessValue": 0, "totInvValue": _r(_f(t.get("total_amount"))),
            "transMode": mode, "transDistance": str(int(_f(tr.get("distance_km")))),
            "transporterId": _s(tr.get("transporter_id")), "transporterName": _s(tr.get("transporter_name")),
            "vehicleNo": _s(tr.get("vehicle_no")).upper().replace(" ", ""), "vehicleType": "R",
            "itemList": [{"itemNo": i["sl"], "productName": i["name"][:100], "productDesc": i["name"][:100],
                          "hsnCode": int(i["hsn"]) if i["hsn"].isdigit() else 0, "quantity": i["qty"], "qtyUnit": i["unit"],
                          "taxableAmount": i["ass"], "cgstRate": 0 if inter else i["gst_rt"] / 2,
                          "sgstRate": 0 if inter else i["gst_rt"] / 2, "igstRate": i["gst_rt"] if inter else 0,
                          "cessRate": 0} for i in items],
        })
    if tot_value_small := [b for b in bills if b["totInvValue"] < 50000]:
        problems.append(f"{len(tot_value_small)} invoice(s) are under ₹50,000 — an e-way bill is usually not required for them.")
    return {"json": {"version": "1.0.0621", "billLists": bills}, "problems": problems, "count": len(bills)}


def save_refs(conn: sqlite3.Connection, ws: str, txn_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Save IRN / Ack no / Ack date / E-Way Bill no on the invoice after uploading on the portal."""
    fields = {k: _s(payload.get(k))[:80] or None for k in ("irn", "ack_no", "ack_date", "eway_bill_no")}
    cur = conn.execute("UPDATE hop_party_transactions SET irn=?, ack_no=?, ack_date=?, eway_bill_no=?, updated_at=datetime('now') "
                       "WHERE workspace_id=? AND id=? AND txn_type=1",
                       (fields["irn"], fields["ack_no"], fields["ack_date"], fields["eway_bill_no"], ws, int(txn_id)))
    if cur.rowcount == 0:
        raise LookupError("Sale invoice not found")
    conn.commit()
    return {"id": int(txn_id), **fields}

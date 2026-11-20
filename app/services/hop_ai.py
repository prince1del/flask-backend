"""AI help for House of Prizm books.

* ``read_bill`` — a photo / PDF of a purchase bill or expense → party, bill no,
  date, items (qty, rate, GST, HSN), total and a likely expense category. One
  Gemini call with a capped output, inside the daily AI limit, logged as usage.
  The result is matched to HoP vendors, items and expense categories; the user
  checks the filled form and saves — nothing is saved by the AI.
* ``books_answer`` — data behind Ask Nexora's ``get_hop_books`` tool: cash &
  bank, who owes us, what we owe, unpaid bills, expenses by category.
"""
from __future__ import annotations

import base64
import json
import re
import sqlite3
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from typing import Any

_PROMPT = (
    "This is a purchase bill / tax invoice / expense receipt received by our business. "
    "Return ONLY JSON: {\"party\":\"\",\"gstin\":\"\",\"bill_no\":\"\",\"bill_date\":\"YYYY-MM-DD\","
    "\"lines\":[{\"item\":\"\",\"hsn\":\"\",\"qty\":0,\"unit\":\"\",\"rate\":0,\"tax_pct\":0,\"free_qty\":0}],"
    "\"charges\":[{\"name\":\"\",\"amount\":0,\"tax_pct\":0}],\"tcs_amount\":0,"
    "\"total\":0,\"expense_category\":\"\",\"po_number\":\"\",\"po_date\":\"YYYY-MM-DD\",\"eway_bill_no\":\"\","
    "\"vehicle_no\":\"\",\"transporter\":\"\",\"utr\":\"\"}. "
    "po_number / po_date = buyer's order no. printed on the bill; eway_bill_no = 12-digit e-way bill no.; "
    "vehicle_no / transporter = transport details; utr = bank / UPI reference if a payment is shown. "
    "charges = freight, packing, loading, labour etc. charged separately (amount before GST); free_qty = free units. "
    "party = the seller who issued the bill (not us). rate = price per unit BEFORE GST. "
    "tax_pct = GST % of that line (CGST+SGST together, or IGST). total = grand total in rupees. "
    "expense_category = one or two words if this is an expense like rent, electricity, petrol, "
    "telephone, transport, repairs, salary; else \"\". Use \"\" or 0 when not on the document. "
    "At most 40 lines."
)


_PROMPT_SALE = (
    "This picture / PDF is something we SELL from: a customer's order, a bill or estimate, a rate list, "
    "or a HANDWRITTEN note or parcha (may be in Hindi, Hinglish or English, may be untidy). Read every item. "
    "Return ONLY JSON: {\"customer\":\"\",\"customer_phone\":\"\",\"gstin\":\"\",\"doc_no\":\"\","
    "\"date\":\"YYYY-MM-DD\",\"lines\":[{\"item\":\"\",\"description\":\"\",\"hsn\":\"\",\"qty\":0,"
    "\"unit\":\"\",\"rate\":0,\"tax_pct\":0,\"discount_pct\":0,\"rate_includes_gst\":false,\"unsure\":false}],"
    "\"total\":0,\"notes\":\"\",\"handwritten\":false}. "
    "customer = the buyer the goods are for (not the seller). item = short product name in English letters "
    "(write Hindi words in English letters). rate = price per unit; if the paper shows a price with GST set "
    "rate_includes_gst true. Use 0 or \"\" for anything not written; never guess a number. "
    "unsure = true when the handwriting of that line is hard to read. qty with unit (MTR, PCS, SET, ROLL, KG). "
    "notes = delivery / payment words written on the paper. At most 40 lines."
)


def _parse_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _num(v: Any) -> float:
    try:
        return round(float(str(v or 0).replace(",", "").replace("₹", "")), 2)
    except ValueError:
        return 0.0


def _similar(a: str, b: str) -> float:
    a, b = re.sub(r"[^a-z0-9]", "", a.lower()), re.sub(r"[^a-z0-9]", "", b.lower())
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def _ask_gemini(data: bytes, mime: str, user_id: int, prompt: str | None = None) -> dict[str, Any]:
    from app.services import nexora_ai_agent as agent
    from app.services.dsr_notes_polish import gemini_api_key

    if agent.over_daily_limit(user_id, "ai"):
        raise ValueError("Daily AI limit reached. Type the bill in by hand.")
    key = gemini_api_key()
    if not key:
        raise ValueError("Bill reading is not set up on the server.")
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("File is too big (max 8 MB).")
    body = json.dumps({
        "contents": [{"role": "user", "parts": [
            {"text": prompt or _PROMPT},
            {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}},
        ]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 3000, "responseMimeType": "application/json"},
    }).encode("utf-8")
    for model in agent.agent_gemini_models():
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            break
        except Exception:
            break
        usage = out.get("usageMetadata") or {}
        agent._log_usage(
            user_id=int(user_id), feature="nexora_agent_hop_bill_read", model=model, rounds=1,
            prompt_tokens=int(usage.get("promptTokenCount") or 0),
            output_tokens=int(usage.get("candidatesTokenCount") or 0),
            total_tokens=int(usage.get("totalTokenCount") or 0), query="hop bill read")
        parts = ((out.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        return _parse_json("".join(p.get("text", "") for p in parts if isinstance(p, dict)))
    raise ValueError("Could not read the bill. Type it in by hand.")


def match_bill(conn: sqlite3.Connection, ws: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Turn the AI's raw fields into form values matched to this company's records."""
    from app import hop_money

    party = str(fields.get("party") or "").strip()[:120]
    gstin = re.sub(r"\s", "", str(fields.get("gstin") or "")).upper()[:15]
    vendor = None
    rows = [dict(r) for r in conn.execute("SELECT * FROM hop_vendors WHERE workspace_id=?", (ws,)).fetchall()]
    if gstin:
        vendor = next((v for v in rows if gstin and str(v.get("gstin") or "").upper() == gstin), None)
    if vendor is None and party:
        best = max(rows, key=lambda v: _similar(party, str(v.get("company") or "")), default=None)
        if best and _similar(party, str(best.get("company") or "")) >= 0.75:
            vendor = best

    products = [dict(r) for r in conn.execute("SELECT id, name, code, hsn, gst_pct FROM hop_products WHERE workspace_id=?",
                                              (ws,)).fetchall()]
    lines = []
    for ln in (fields.get("lines") or [])[:40]:
        if not isinstance(ln, dict):
            continue
        name = str(ln.get("item") or "").strip()[:120]
        qty, rate = _num(ln.get("qty")), _num(ln.get("rate"))
        if not name or qty <= 0:
            continue
        prod = max(products, key=lambda p: _similar(name, str(p.get("name") or "")), default=None)
        if prod and _similar(name, str(prod.get("name") or "")) < 0.8:
            prod = None
        lines.append({
            "item_name": prod["name"] if prod else name,
            "product_id": prod["id"] if prod else None,
            "matched_item": bool(prod),
            "hsn": str(ln.get("hsn") or (prod or {}).get("hsn") or "").strip()[:12],
            "qty": qty, "unit": str(ln.get("unit") or "Pcs").strip()[:12] or "Pcs",
            "rate": rate, "tax_pct": _num(ln.get("tax_pct")), "free_qty": _num(ln.get("free_qty")),
        })
    charges = []
    for c in (fields.get("charges") or [])[:10]:
        if isinstance(c, dict) and str(c.get("name") or "").strip() and _num(c.get("amount")) > 0:
            charges.append({"name": str(c["name"]).strip()[:60], "amount": _num(c.get("amount")),
                            "tax_pct": _num(c.get("tax_pct"))})

    cat_name = str(fields.get("expense_category") or "").strip()
    category = None
    if cat_name:
        cats = hop_money.list_expense_categories(conn, ws)
        category = max(cats, key=lambda c: _similar(cat_name, c["name"]), default=None)
        if category and _similar(cat_name, category["name"]) < 0.6:
            category = None
    date = str(fields.get("bill_date") or "")
    total = _num(fields.get("total"))
    tcs = _num(fields.get("tcs_amount"))
    computed = round(sum(l["qty"] * l["rate"] * (1 + l["tax_pct"] / 100) for l in lines)
                     + sum(c["amount"] * (1 + c["tax_pct"] / 100) for c in charges) + tcs, 2)
    notes = []
    if party and not vendor:
        notes.append(f"Seller \"{party}\" is not in your vendors yet — add it, then pick it.")
    if lines and total and abs(computed - total) > max(2.0, total * 0.01):
        notes.append(f"Items add up to ₹{computed:,.2f} but the bill total is ₹{total:,.2f}. Check rates / GST.")
    unmatched = [l["item_name"] for l in lines if not l["matched_item"]]
    if unmatched:
        notes.append(f"{len(unmatched)} item(s) are not in your catalogue, so they will not move stock.")
    return {
        "party_name": party, "gstin": gstin,
        "vendor_id": vendor["id"] if vendor else None, "vendor_name": vendor.get("company") if vendor else None,
        "bill_no": str(fields.get("bill_no") or "").strip()[:40],
        "bill_date": date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else "",
        "lines": lines, "charges": charges, "tcs_amount": tcs, "total": total, "items_total": computed,
        # transport / order / payment details printed on the bill (filled into the form, user checks and saves)
        "po_number": str(fields.get("po_number") or "").strip()[:40],
        "po_date": (lambda d: d if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else "")(str(fields.get("po_date") or "")),
        "eway_bill_no": re.sub(r"\D", "", str(fields.get("eway_bill_no") or ""))[:12],
        "vehicle_no": str(fields.get("vehicle_no") or "").strip().upper()[:20],
        "transporter": str(fields.get("transporter") or "").strip()[:60],
        "utr": str(fields.get("utr") or "").strip()[:40],
        "expense_category_id": category["id"] if category else None,
        "expense_category": category["name"] if category else cat_name,
        "checks": notes,
    }


def read_bill(conn: sqlite3.Connection, ws: str, data: bytes, mime: str, *, user_id: int,
              kind: str = "purchase") -> dict[str, Any]:
    """kind = purchase (bill / expense we received) | sale (invoice) | quote (quotation / estimate)."""
    if kind in ("sale", "quote"):
        return match_sale(conn, ws, _ask_gemini(data, mime, user_id, _PROMPT_SALE), kind)
    return match_bill(conn, ws, _ask_gemini(data, mime, user_id))


def match_sale(conn: sqlite3.Connection, ws: str, fields: dict[str, Any], kind: str = "sale") -> dict[str, Any]:
    """AI-read order / handwritten note → Sale Invoice or Quotation form, matched to this firm's
    customers and items. Missing rates / GST come from the catalogue and are marked; nothing is saved."""
    from app import hop_items

    name = str(fields.get("customer") or "").strip()[:120]
    phone = re.sub(r"\D", "", str(fields.get("customer_phone") or ""))[-10:]
    gstin = re.sub(r"\s", "", str(fields.get("gstin") or "")).upper()[:15]
    custs = [dict(r) for r in conn.execute("SELECT * FROM hop_customers WHERE workspace_id=?", (ws,)).fetchall()]
    cust = None
    if gstin:
        cust = next((c for c in custs if str(c.get("gst_no") or "").upper() == gstin), None)
    if cust is None and phone:
        cust = next((c for c in custs if re.sub(r"\D", "", str(c.get("mobile") or ""))[-10:] == phone), None)
    if cust is None and name:
        best = max(custs, key=lambda c: _similar(name, str(c.get("company") or "")), default=None)
        if best and _similar(name, str(best.get("company") or "")) >= 0.75:
            cust = best
    products = [dict(r) for r in conn.execute(
        "SELECT * FROM hop_products WHERE workspace_id=? AND (deleted_at IS NULL OR trim(deleted_at)='')", (ws,)).fetchall()]
    lines, notes = [], []
    for ln in (fields.get("lines") or [])[:40]:
        if not isinstance(ln, dict):
            continue
        item = str(ln.get("item") or "").strip()[:120]
        qty = _num(ln.get("qty")) or 1.0
        if not item:
            continue
        prod = max(products, key=lambda p: _similar(item, str(p.get("name") or "")), default=None)
        if prod and _similar(item, str(prod.get("name") or "")) < 0.75:
            prod = None
        rate, tax = _num(ln.get("rate")), _num(ln.get("tax_pct"))
        rate_from = "paper"
        if not rate and prod:
            r2, src = hop_items.price_for_qty(prod, qty, "customer")
            rate, rate_from = float(r2 or 0), (src or "catalogue")
        if not tax and prod:
            tax = float(prod.get("gst_pct") or 0)
        if ln.get("rate_includes_gst") and rate and tax:
            rate = round(rate / (1 + tax / 100.0), 2)
        if not rate:
            rate_from = "missing"
        lines.append({"item_name": prod["name"] if prod else item, "product_id": prod["id"] if prod else None,
                      "matched_item": bool(prod), "description": str(ln.get("description") or "").strip()[:200],
                      "hsn": str(ln.get("hsn") or (prod or {}).get("hsn") or "").strip()[:12], "qty": qty,
                      "unit": (str(ln.get("unit") or (prod or {}).get("unit") or "PCS").strip()[:12] or "PCS").upper(),
                      "rate": rate, "tax_pct": tax, "discount_pct": _num(ln.get("discount_pct")),
                      "rate_from": rate_from, "unsure": bool(ln.get("unsure"))})
    if name and not cust:
        notes.append(f"Customer \"{name}\" is not in your parties yet — add it, then pick it.")
    if not name and not cust:
        notes.append("No customer name on the paper — pick the customer.")
    missing = [l["item_name"] for l in lines if l["rate_from"] == "missing"]
    if missing:
        notes.append("No rate for: " + ", ".join(missing[:6]) + ". Type the rate.")
    cat = [l["item_name"] for l in lines if l["rate_from"] in ("catalogue", "wholesale")]
    if cat:
        notes.append("Rate taken from your catalogue for: " + ", ".join(cat[:6]) + ".")
    unsure = [l["item_name"] for l in lines if l["unsure"]]
    if unsure:
        notes.append("Hard to read, please check: " + ", ".join(unsure[:6]) + ".")
    computed = round(sum(l["qty"] * l["rate"] * (1 - l["discount_pct"] / 100) * (1 + l["tax_pct"] / 100) for l in lines), 2)
    total = _num(fields.get("total"))
    if lines and total and abs(computed - total) > max(2.0, total * 0.01):
        notes.append(f"Items add up to ₹{computed:,.2f} but the paper says ₹{total:,.2f}. Check rates / GST.")
    date = str(fields.get("date") or "")
    return {
        "kind": kind, "customer_name": name, "customer_id": cust["id"] if cust else None,
        "customer_match": cust.get("company") if cust else None, "customer_phone": phone, "gstin": gstin,
        "doc_no": str(fields.get("doc_no") or "").strip()[:40],
        "date": date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else "",
        "lines": lines, "total": total, "items_total": computed,
        "notes": str(fields.get("notes") or "").strip()[:300], "handwritten": bool(fields.get("handwritten")),
        "checks": notes,
    }


# ── Ask Nexora: get_hop_books ────────────────────────────────────────────────

def _godown_summary(conn, ws, q: str = "") -> list[dict[str, Any]]:
    from app import hop_godowns

    gs = hop_godowns.list_godowns(conn, ws)
    if len(gs) < 2:
        return []
    names = {g["id"]: g["name"] for g in gs}
    stock = [s for s in hop_godowns.stock_by_godown(conn, ws) if not q or q.lower() in s["name"].lower()][:20]
    return [{"godown": g["name"], "items": g["items"], "stock_value": g["stock_value"]} for g in gs] + [
        {"item": s["name"], "by_godown": {names.get(k, k): v for k, v in s["by_godown"].items() if v}} for s in stock]


def books_answer(conn: sqlite3.Connection, ws: str, what: str, party_name: str = "",
                 extra: dict[str, Any] | None = None) -> dict[str, Any]:
    from app import hop_books, hop_money

    conn.row_factory = sqlite3.Row
    extra = extra or {}
    what = (what or "overview").strip().lower()
    party_name = (party_name or "").strip()

    def outstanding(txn_type: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            """SELECT party_name, COUNT(*) AS bills, ROUND(SUM(balance_amount), 2) AS due,
                      MIN(date(txn_date)) AS oldest
               FROM hop_party_transactions WHERE (deleted_at IS NULL OR trim(deleted_at)='') AND workspace_id=? AND txn_type=? AND balance_amount > 0.009
               GROUP BY party_name ORDER BY due DESC""", (ws, txn_type)).fetchall()
        out = [dict(r) for r in rows]
        if party_name:
            out = [r for r in out if _similar(party_name, r["party_name"] or "") >= 0.6]
        return out

    if what == "cash_bank":
        accts = hop_books.list_bank_accounts(conn, ws)
        cheques = hop_money.list_cheques(conn, ws, "open")
        from app import hop_reconcile

        recon = []
        for a in accts:
            if a.get("account_type") != "cash":
                st = hop_reconcile.statement(conn, ws, a["id"])
                recon.append({"account": a["name"], "bank_balance_ticked": st["cleared_balance"],
                              "not_yet_ticked_in": st["uncleared_in"], "not_yet_ticked_out": st["uncleared_out"]})
        return {"accounts": [{"name": a["name"], "type": a["account_type"], "balance": a["current_balance"]} for a in accts],
                "reconciliation": recon,
                "total": round(sum(float(a["current_balance"] or 0) for a in accts), 2),
                "open_cheques": [{"party": c["party_name"], "amount": c["amount"], "cheque_no": c["cheque_no"],
                                  "type": "received" if c["direction"] == "in" else "issued"} for c in cheques]}
    if what == "receivables":
        rows = outstanding(1)
        return {"who_owes_us": rows[:25], "total": round(sum(r["due"] for r in rows), 2)}
    if what == "payables":
        rows = outstanding(2)
        return {"we_owe": rows[:25], "total": round(sum(r["due"] for r in rows), 2)}
    if what == "unpaid_bills":
        sql = """SELECT txn_label, txn_number, date(txn_date) AS date, party_name, total_amount, balance_amount, status_text
                 FROM hop_party_transactions WHERE (deleted_at IS NULL OR trim(deleted_at)='') AND workspace_id=? AND txn_type IN (1, 2) AND balance_amount > 0.009
                 ORDER BY date(txn_date) LIMIT 60"""
        rows = [dict(r) for r in conn.execute(sql, (ws,)).fetchall()]
        if party_name:
            rows = [r for r in rows if _similar(party_name, r["party_name"] or "") >= 0.6]
        return {"bills": rows[:30]}
    if what in ("profit_loss", "balance_sheet", "day_book", "party_statement", "gst"):
        from datetime import date as _date

        from app import hop_reports

        if what == "profit_loss":
            return hop_reports.profit_and_loss(conn, ws, str(extra.get("from") or ""), str(extra.get("to") or ""))
        if what == "balance_sheet":
            return hop_reports.balance_sheet(conn, ws)
        if what == "day_book":
            return hop_reports.day_book(conn, ws, str(extra.get("date") or _date.today().isoformat())[:10])
        if what == "gst":
            return hop_reports.gstr3b(conn, ws, str(extra.get("from") or ""), str(extra.get("to") or ""))
        if not party_name:
            return {"error": "Which party? Give a party name."}
        best = None
        scored = []
        for kind, table in (("customer", "hop_customers"), ("vendor", "hop_vendors")):
            for r in conn.execute(f"SELECT id, company FROM {table} WHERE workspace_id=?", (ws,)).fetchall():
                sc = _similar(party_name, r[1] or "")
                scored.append((sc, r[1] or ""))
                if sc >= 0.6 and (best is None or sc > best[0]):
                    best = (sc, kind, int(r[0]))
        if not best:
            closest = [n for _, n in sorted(scored, reverse=True)[:5] if n]
            return {"not_found": party_name, "parties_in_books": len(scored), "closest_names": closest,
                    "tell_user": f"There is no party named '{party_name}' in this business's books. "
                                 "Say so plainly and offer the closest names; do not guess figures."}
        f_, t_ = str(extra.get("from") or ""), str(extra.get("to") or "")
        stmt = hop_reports.party_statement(conn, ws, best[1], best[2], f_, t_)
        stmt["lines"] = stmt["lines"][-30:]
        # "X ka sale kitna tha" for a period: sale bills − sale returns (purchase side for a supplier), by bill date.
        sale_t, ret_t = (1, 21) if best[1] == "customer" else (2, 16)
        cond, args = "", [ws, best[1], best[2]]
        if f_:
            cond += " AND date(txn_date) >= date(?)"; args.append(f_)
        if t_:
            cond += " AND date(txn_date) <= date(?)"; args.append(t_)
        tot = {int(r[0]): float(r[1] or 0) for r in conn.execute(
            "SELECT txn_type, SUM(total_amount) FROM hop_party_transactions WHERE workspace_id=? AND party_type=? AND party_id=? "
            f"AND (deleted_at IS NULL OR trim(deleted_at)='') AND txn_type IN ({sale_t}, {ret_t}){cond} GROUP BY txn_type",
            args).fetchall()}
        label = "sales" if best[1] == "customer" else "purchases"
        stmt[f"{label}_in_period"] = round(tot.get(sale_t, 0.0), 2)
        stmt["returns_in_period"] = round(tot.get(ret_t, 0.0), 2)
        stmt[f"net_{label}_in_period"] = round(tot.get(sale_t, 0.0) - tot.get(ret_t, 0.0), 2)
        stmt["period"] = {"from": f_ or "start", "to": t_ or "today"}
        return stmt
    if what == "items":
        from app import hop_ops

        q = str(party_name or "").strip()
        rows = hop_ops.list_products(conn, ws, q or None)
        def slim(r):
            return {k: r.get(k) for k in ("name", "code", "barcode", "item_type", "unit", "stock_qty", "selling_price",
                                          "purchase_price", "mrp", "wholesale_price", "wholesale_min_qty", "alt_unit",
                                          "alt_unit_factor", "hsn", "gst_pct", "custom_fields") if r.get(k) not in (None, "", {})}
        goods = [r for r in rows if r.get("item_type") != "service"]
        return {"items": [slim(r) for r in rows[:25]], "count": len(rows),
                "low_or_negative_stock": [{"name": r["name"], "stock": r.get("stock_qty")} for r in goods
                                          if float(r.get("stock_qty") or 0) <= 0][:15],
                "item_problems": [{"name": r["name"], "problems": r["checks"]} for r in rows if r.get("checks")][:15],
                "godowns": _godown_summary(conn, ws, q)}
    if what == "reminders":
        from app import hop_reminders

        dl = hop_reminders.due_list(conn, ws)
        slim = lambda xs: [{k: x[k] for k in ("party", "number", "balance", "due_date", "days")} for x in xs[:20]]  # noqa: E731
        return {"overdue": slim(dl["overdue"]), "due_today": slim(dl["today"]), "due_soon": slim(dl["soon"]),
                "totals": dl["totals"], "as_of": dl["as_of"]}
    if what.startswith("dist_"):
        from app import hop_distributor as D

        D.ensure_schema(conn)
        if what == "dist_orders":
            pl = D.pipeline(conn, ws)
            keep = ("txn_number", "txn_date", "total_amount", "status", "days_waiting", "pending_amount")
            return {"counts": pl["counts"],
                    "open_orders": [{**{k: o.get(k) for k in keep}, "sos": [s["so_number"] for s in o["sos"]],
                                     "cis": [b.get("party_bill_no") or b["txn_number"] for b in o["bills"]]}
                                    for o in pl["orders"] if o["status"] != "Billed"][:25],
                    "sos_without_order": [s["so_number"] for s in pl["sos_without_order"]][:20]}
        if what == "dist_company_ledger":
            pr = D.principals(conn, ws)
            if not pr:
                return {"note": "No company marked as principal yet (Orders → SO → CI → Set company)."}
            out = []
            for p in pr:
                lg = D.company_ledger(conn, ws, int(p["id"]), str(extra.get("from") or ""), str(extra.get("to") or ""))
                out.append({"company": p["company"], **lg["summary"],
                            "open_bills": [{k: b.get(k) for k in ("txn_number", "party_bill_no", "txn_date", "due_date",
                                                                   "balance_amount", "overdue_days")} for b in lg["open_bills"][:15]]})
            return {"companies": out}
        if what == "dist_retailer_goods":
            rg = D.retailer_goods(conn, ws, str(extra.get("from") or ""), str(extra.get("to") or ""), q=party_name or "")
            return {"from": rg["from"], "to": rg["to"], "total_qty": rg["total_qty"], "total_amount": rg["total_amount"],
                    "retailers": [{"retailer": r["party"], "qty": r["qty"], "amount": r["amount"], "bills": r["bills"],
                                   "items": [{k: i[k] for k in ("item", "qty", "amount", "last_date")} for i in r["items"][:8]]}
                                  for r in rg["retailers"][:15]]}
        if what == "dist_retailer_forecast":
            rf = D.retailer_forecast(conn, ws)
            rets = rf["retailers"]
            if party_name:
                rets = [r for r in rets if _similar(party_name, r["party"] or "") >= 0.5]
            keep = ("item", "every_days", "usual_qty", "last_date", "next_date", "days_late", "status")
            return {"note": rf["note"],
                    "due_now": [{"retailer": r["party"], **{k: r[k] for k in keep}} for r in rf["due_now"][:20]],
                    "due_soon": [{"retailer": r["party"], **{k: r[k] for k in keep}} for r in rf["due_soon"][:20]],
                    "retailers": [{"retailer": r["party"], "buys_every_days": r["buys_every_days"], "amount": r["amount"],
                                   "top_items": [{k: i[k] for k in keep} for i in r["top_items"][:6]]} for r in rets[:12]]}
        if what == "dist_forecast":
            fc = D.forecast(conn, ws)
            keep = ("item", "stock", "on_order", "per_day", "days_left", "order_qty", "order_by", "flag", "trend_pct")
            return {**{k: fc[k] for k in ("order_now", "order_soon", "no_sale", "suggested_value", "lookback_days", "cover_days")},
                    "items": [{k: i[k] for k in keep} for i in fc["items"] if i["flag"] != "OK"][:25]}
        if what == "dist_warehouse":
            wh = D.warehouse(conn, ws)
            items = wh["items"]
            if party_name:
                items = [i for i in items if _similar(party_name, str(i.get("item") or "")) >= 0.5]
            return {"total_value": wh["total_value"], "out_of_stock": wh["out_of_stock"], "count": wh["count"],
                    "items": [{k: i[k] for k in ("item", "code", "qty", "stock_value")} for i in items[:40]]}
        if what == "dist_price_changes":
            from app import hop_dist_extra as X

            pc = X.price_changes(conn, ws, int(extra.get("days") or 30))
            rows = pc["changes"]
            if party_name:
                rows = [r for r in rows if _similar(party_name, f"{r.get('code') or ''} {r['name']}") >= 0.5
                        or (r.get("code") and party_name.lower() in str(r["code"]).lower())]
            keep = ("code", "name", "field", "old", "new", "pct", "at", "source", "stock_effect")
            return {k: pc[k] for k in ("days", "count", "ups", "downs", "articles_changed", "stock_value_effect", "categories")} | {
                "changes": [{k: r.get(k) for k in keep} for r in rows[:30]], "margin_alerts": pc["margin_alerts"][:15]}
        if what in ("dist_articles", "dist_retailer_dues", "dist_profit", "dist_staff"):
            from app import hop_dist_extra as X

            X.ensure_schema(conn)
            if what == "dist_articles":
                a = X.articles(conn, ws, party_name or "")
                keep = ("code", "name", "category", "season", "mrp", "purchase_price", "selling_price", "margin_pct", "stock_qty",
                        "details")   # details: TC, size, shade, ply, bale pack … from the company sheet
                return {"count": a["count"], "problems": a["problems"], "articles": [{k: r.get(k) for k in keep} for r in a["articles"][:40]]}
            if what == "dist_retailer_dues":
                d = X.retailer_dues(conn, ws)
                rows = d["retailers"]
                if party_name:
                    rows = [r for r in rows if _similar(party_name, r["party"] or "") >= 0.5]
                keep = ("party", "due", "overdue", "oldest_days", "buckets", "broken_promises", "why")
                return {"total_due": d["total_due"], "total_overdue": d["total_overdue"], "call_today": d["call_today"],
                        "retailers": [{**{k: r.get(k) for k in keep},
                                       "last_payment": r.get("last_payment"), "open_promise": (r.get("open_promise") or {}).get("promise_date")}
                                      for r in rows[:25]]}
            if what == "dist_profit":
                p = X.profitability(conn, ws, str(extra.get("from") or ""), str(extra.get("to") or ""))
                return {"from": p["from"], "to": p["to"], "totals": p["totals"], "months": p["months"],
                        "top_items": p["items"][:10], "low_margin_items": p["low_margin_items"][:10],
                        "sold_below_cost": p["sold_below_cost"][:10], "companies": p["companies"], "top_retailers": p["retailers"][:10]}
            st = X.staff_performance(conn, ws, str(extra.get("from") or ""), str(extra.get("to") or ""))
            return st
        h = D.home(conn, ws)
        return {k: v for k, v in h.items() if k != "principals"} | {"companies": [p["company"] for p in h["principals"]]}
    if what == "salary":
        from app import hop_salary as SAL

        sheet = SAL.month_sheet(conn, ws, str(extra.get("month") or extra.get("from") or "")[:7])
        rows = sheet["staff"]
        if party_name:
            rows = [r for r in rows if _similar(party_name, r["name"] or "") >= 0.5]
        return {"month": sheet["month"], "totals": sheet["totals"], "checks": sheet["checks"],
                "staff": [{k: r.get(k) for k in ("name", "designation", "salary", "pay_type", "present_days", "earned",
                                                 "advance_due", "paid")} | {"net_paid": (r.get("run") or {}).get("net"),
                                                                            "pf_esi_pt_tds": r.get("working")}
                          for r in rows[:30]],
                "by_month": SAL.year_summary(conn, ws, 6), "dues": sheet["dues"], "unpaid_dues": SAL.open_dues(conn, ws)}
    if what == "notes":
        from app import hop_notes

        rows = hop_notes.search(conn, ws, "", party_name or "", 40)
        return {"notes": [{k: r.get(k) for k in ("title", "body", "party_name", "remind_on", "pinned", "updated_at")} for r in rows]}
    if what == "commission":
        from app import hop_ops

        rec = hop_ops.list_commission_records(conn, ws, agent_name=party_name or None)
        due = [r for r in hop_ops.list_tax_invoices_for_commission(conn, ws) if r.get("commission_due")]
        return {"summary": rec.get("summary"),
                "records": [{k: r.get(k) for k in ("agent_name", "invoice_no", "party_name", "commission_pct", "commission_amount",
                                                   "tds_amount", "net_commission", "payment_status", "paid_on")}
                            for r in rec.get("records", [])[:30]],
                "bills_commission_not_entered": [{"invoice_no": r["invoice_no"], "party": r["party_name"],
                                                  "usual_agent": r.get("suggested_agent")} for r in due[:20]]}
    if what in ("dispatch", "samples", "complaints"):
        from app import hop_ops

        def hit(*vals: Any) -> bool:
            return not party_name or any(v and _similar(party_name, str(v)) >= 0.6 for v in vals)

        if what == "dispatch":
            rows = [{"bill_or_challan": d.get("invoice_no_link"), "customer": d.get("bill_party"), "courier": d.get("courier"),
                     "tracking": d.get("tracking_number"), "eway_bill": d.get("eway_bill"), "sent": d.get("dispatched_at"),
                     "status": d.get("delivery_status") or d.get("status"), "delivered": d.get("delivered_at")}
                    for d in hop_ops.list_dispatches(conn, ws)
                    if hit(d.get("bill_party"), d.get("invoice_no_link"), d.get("project_name"))]
            return {"dispatches": rows[:30], "not_delivered": sum(1 for r in rows if (r["status"] or "") != "delivered")}
        if what == "samples":
            rows = [{"sample": s.get("sample_name"), "customer": s.get("customer_company"), "sent": s.get("sent_at"),
                     "courier": s.get("courier"), "approval": s.get("approval_status") or "pending", "return": s.get("return_status")}
                    for s in hop_ops.list_samples(conn, ws) if hit(s.get("customer_company"), s.get("sample_name"))]
            return {"samples": rows[:30]}
        rows = [{"issue": c.get("issue"), "customer": c.get("customer_company"), "status": c.get("status"),
                 "priority": c.get("priority"), "date": c.get("complaint_date"), "assigned_to": c.get("assigned_to")}
                for c in hop_ops.list_complaints(conn, ws) if hit(c.get("customer_company"))]
        return {"complaints": rows[:30], "open": sum(1 for r in rows if (r["status"] or "open") != "resolved")}
    if what == "targets":
        from app import hop_targets

        s = hop_targets.summary(conn, ws)
        return {k: s[k] for k in ("fy", "yearly_target", "achieved_ytd", "yearly_pct", "gap", "per_month_needed", "current_month")} | {
            "months": [{k: m[k] for k in ("label", "target", "achieved", "pct")} for m in s["months"] if not m["future"]]}
    if what == "online_orders":
        from app import hop_catalog_link

        return hop_catalog_link.summary_for_ai(conn, ws)
    if what == "service":
        from app import hop_service_reminders

        return hop_service_reminders.summary_for_ai(conn, ws, str(party_name or "").strip())
    if what == "loyalty":
        from app import hop_loyalty

        return hop_loyalty.top_customers(conn, ws, str(party_name or "").strip())
    if what == "manufacturing":
        from app import hop_manufacture

        return hop_manufacture.summary_for_ai(conn, ws, str(party_name or "").strip())
    if what == "audit":
        from app import hop_audit

        q = str(party_name or "").strip()
        log = hop_audit.list_log(conn, ws, q=q, frm=str(extra.get("from") or ""), to=str(extra.get("to") or ""), limit=40)
        return {"changes": [{"when": r["at"], "what": r["text"]} for r in log["rows"]], "people": log["users"]}
    if what == "other_income":
        cats = hop_money.list_expense_categories(conn, ws, "income")
        return {"by_category": [{"category": c["name"], "total": c["total"]} for c in cats if c["total"]],
                "total": round(sum(float(c["total"] or 0) for c in cats), 2)}
    if what == "expenses":
        cats = hop_money.list_expense_categories(conn, ws)
        return {"by_category": [{"category": c["name"], "total": c["total"]} for c in cats if c["total"]],
                "total": round(sum(float(c["total"] or 0) for c in cats), 2)}
    # overview
    rec, pay = outstanding(1), outstanding(2)
    accts = hop_books.list_bank_accounts(conn, ws)
    return {"cash_and_bank": round(sum(float(a["current_balance"] or 0) for a in accts), 2),
            "receivable_total": round(sum(r["due"] for r in rec), 2), "top_receivables": rec[:5],
            "payable_total": round(sum(r["due"] for r in pay), 2), "top_payables": pay[:5]}


# ── "AI insights" on a report ────────────────────────────────────────────────

_INSIGHT_PROMPT = (
    "You are an accountant helping a small Indian furnishing business owner. Below is a report from "
    "their books as JSON (amounts in rupees). Give 3 to 5 short, practical points in simple English: "
    "what stands out, anything worrying (losses, unpaid amounts, high expenses, negative cash, GST to pay), "
    "and one action to take. Use only numbers from the report; do not invent any. "
    "Return ONLY JSON: {\"points\":[\"…\"]}."
)


def _slim(data: Any, depth: int = 0) -> Any:
    """Keep the prompt small: cap long lists, drop ids."""
    if isinstance(data, dict):
        return {k: _slim(v, depth + 1) for k, v in data.items() if k not in ("id", "txn_id", "party_id", "product_id")}
    if isinstance(data, list):
        return [_slim(v, depth + 1) for v in data[:25]] + ([f"… {len(data) - 25} more"] if len(data) > 25 else [])
    return data


def payment_reminder_text(
    bill: dict[str, Any],
    *,
    firm_name: str = "",
    user_id: int,
) -> dict[str, Any]:
    """WhatsApp-ready payment reminder. Uses Gemini when available; else a clear template."""
    party = str(bill.get("party_name") or "Sir/Madam").strip()
    bill_no = str(bill.get("txn_number") or "").strip() or "your bill"
    due = str(bill.get("due_date") or "").strip() or "soon"
    bal = bill.get("balance_amount")
    try:
        bal_s = f"₹{float(bal):,.2f}"
    except (TypeError, ValueError):
        bal_s = "the pending amount"
    firm = (firm_name or "us").strip()
    template = (
        f"Namaste {party},\n\n"
        f"Friendly reminder from {firm}: invoice {bill_no} for {bal_s} was due on {due}. "
        f"Please arrange payment at your earliest convenience.\n\nThank you."
    )
    from app.services import nexora_ai_agent as agent
    from app.services.dsr_notes_polish import gemini_api_key

    key = gemini_api_key()
    if not key or agent.over_daily_limit(user_id, "ai"):
        return {"text": template, "source": "template"}
    prompt = (
        "Write a short polite WhatsApp payment reminder in simple English (optionally 1 Hindi greeting). "
        "Max 500 characters. No markdown. Use only these facts — do not invent amounts or dates:\n"
        f"party={party}; firm={firm}; bill={bill_no}; due={due}; balance={bal_s}\n"
        'Return ONLY JSON: {"text":"…"}'
    )
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 250, "responseMimeType": "application/json"},
    }).encode("utf-8")
    for model in agent.agent_gemini_models():
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue
        usage = out.get("usageMetadata") or {}
        agent._log_usage(user_id=int(user_id), feature="nexora_agent_hop_payment_reminder", model=model, rounds=1,
                         prompt_tokens=int(usage.get("promptTokenCount") or 0),
                         output_tokens=int(usage.get("candidatesTokenCount") or 0),
                         total_tokens=int(usage.get("totalTokenCount") or 0), query="hop payment reminder")
        parts = ((out.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = str(_parse_json("".join(p.get("text", "") for p in parts if isinstance(p, dict))).get("text") or "").strip()
        if text:
            return {"text": text[:700], "source": "ai"}
    return {"text": template, "source": "template"}


def ask_json(prompt: str, *, user_id: int, feature: str, max_tokens: int = 400) -> dict[str, Any]:
    """One capped text-only Gemini call inside the daily AI limit; usage logged. Returns parsed JSON."""
    from app.services import nexora_ai_agent as agent
    from app.services.dsr_notes_polish import gemini_api_key

    if agent.over_daily_limit(user_id, "ai"):
        raise ValueError("Daily AI limit reached.")
    key = gemini_api_key()
    if not key:
        raise ValueError("AI is not set up on the server.")
    body = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt[:12000]}]}],
                       "generationConfig": {"temperature": 0.2, "maxOutputTokens": int(max_tokens),
                                            "responseMimeType": "application/json"}}).encode("utf-8")
    for model in agent.agent_gemini_models():
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            break
        except Exception:
            break
        usage = out.get("usageMetadata") or {}
        agent._log_usage(user_id=int(user_id), feature=feature, model=model, rounds=1,
                         prompt_tokens=int(usage.get("promptTokenCount") or 0),
                         output_tokens=int(usage.get("candidatesTokenCount") or 0),
                         total_tokens=int(usage.get("totalTokenCount") or 0), query=feature)
        parts = ((out.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        return _parse_json("".join(p.get("text", "") for p in parts if isinstance(p, dict)))
    raise ValueError("AI is not available right now.")


def suggest_bom(conn: sqlite3.Connection, ws: str, product_id: int, *, user_id: int) -> dict[str, Any]:
    """✨ Raw materials for one piece of an item, picked from the firm's own item list. Nothing is saved."""
    item = conn.execute("SELECT name, unit, description FROM hop_products WHERE workspace_id=? AND id=?",
                        (ws, int(product_id))).fetchone()
    if not item:
        raise ValueError("Item not found")
    cat = conn.execute("SELECT id, name, unit FROM hop_products WHERE workspace_id=? AND id<>? "
                       "AND (deleted_at IS NULL OR deleted_at='') ORDER BY name LIMIT 400", (ws, int(product_id))).fetchall()
    names = "\n".join(f"{r[0]}|{r[1]}|{r[2] or ''}" for r in cat)
    prompt = (
        "You help a small Indian business set up manufacturing. For ONE piece of the finished item below, list the raw "
        "materials and quantity per piece. Prefer items from the catalogue (id|name|unit); use the catalogue id when one "
        "fits. Use realistic quantities in the catalogue unit. If something needed is not in the catalogue, give its name "
        "with id 0. Max 8 lines.\n"
        f"Finished item: {item[0]} (unit {item[1] or 'pcs'}) {item[2] or ''}\nCatalogue:\n{names}\n"
        'Return ONLY JSON: {"raw":[{"id":12,"name":"…","qty":2.5,"why":"short reason"}]}'
    )
    out = ask_json(prompt, user_id=user_id, feature="nexora_agent_hop_bom", max_tokens=400)
    ids = {r[0]: (r[1], r[2] or "") for r in cat}
    rows, missing = [], []
    for r in (out.get("raw") or [])[:8]:
        qty = _num(r.get("qty"))
        rid = int(_num(r.get("id")))
        if rid not in ids:
            # Match by name when the id is wrong or 0.
            best = max(cat, key=lambda c: _similar(str(r.get("name") or ""), c[1] or ""), default=None)
            rid = best[0] if best and _similar(str(r.get("name") or ""), best[1] or "") >= 0.75 else 0
        if qty <= 0:
            continue
        if rid:
            rows.append({"raw_product_id": rid, "name": ids[rid][0], "unit": ids[rid][1], "qty": round(qty, 3),
                         "why": str(r.get("why") or "")[:120]})
        else:
            missing.append({"name": str(r.get("name") or "")[:80], "qty": round(qty, 3)})
    return {"raw": rows, "not_in_items": missing,
            "note": "Check the quantities before saving. Items not in your list must be added first."}


def report_insights(title: str, data: dict[str, Any], *, user_id: int) -> list[str]:
    from app.services import nexora_ai_agent as agent
    from app.services.dsr_notes_polish import gemini_api_key

    if agent.over_daily_limit(user_id, "ai"):
        raise ValueError("Daily AI limit reached.")
    key = gemini_api_key()
    if not key:
        raise ValueError("AI is not set up on the server.")
    text = f"{_INSIGHT_PROMPT}\n\nReport: {title}\n{json.dumps(_slim(data), ensure_ascii=False)[:12000]}"
    body = json.dumps({"contents": [{"role": "user", "parts": [{"text": text}]}],
                       "generationConfig": {"temperature": 0.2, "maxOutputTokens": 500,
                                            "responseMimeType": "application/json"}}).encode("utf-8")
    for model in agent.agent_gemini_models():
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            break
        except Exception:
            break
        usage = out.get("usageMetadata") or {}
        agent._log_usage(user_id=int(user_id), feature="nexora_agent_hop_insights", model=model, rounds=1,
                         prompt_tokens=int(usage.get("promptTokenCount") or 0),
                         output_tokens=int(usage.get("candidatesTokenCount") or 0),
                         total_tokens=int(usage.get("totalTokenCount") or 0), query=f"hop insights {title}")
        parts = ((out.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        pts = _parse_json("".join(p.get("text", "") for p in parts if isinstance(p, dict))).get("points") or []
        return [str(p).strip()[:300] for p in pts if str(p).strip()][:5]
    raise ValueError("AI insights are not available right now.")


# ── Distributor: company price list + company ledger (SOA) readers (one capped call, daily AI limit) ─────
_PROMPT_PRICE_LIST = """You read a company's price list (PDF or photo) for a distributor.
Return ONLY JSON: {"season": "", "rows": [{"code": "", "name": "", "category": "", "mrp": 0, "dp": 0, "retailer_price": 0}]}
- code = article / item / design number; name = article name as printed; category = range / group if printed.
- mrp = MRP; dp = the distributor's buying price (DP / NLC / dealer / billing rate); retailer_price = price to retailers
  (RLP / wholesale) if printed. Numbers only, no commas or ₹. Leave 0 when not printed. Never invent rows."""

_PROMPT_SOA = """You read a company's ledger / statement of account for one of its customers (a distributor).
Return ONLY JSON: {"rows": [{"date": "YYYY-MM-DD", "doc_no": "", "narration": "", "debit": 0, "credit": 0}],
"opening_balance": 0, "closing_balance": 0}
- debit = invoices / debit notes to the customer; credit = payments received, credit notes, discounts.
- doc_no = invoice / document / reference number as printed. Numbers only. Skip total and balance rows. Never invent rows."""


def read_price_list(data: bytes, mime: str, *, user_id: int) -> list[dict[str, Any]]:
    out = _ask_gemini(data, mime, user_id, _PROMPT_PRICE_LIST)
    season = str(out.get("season") or "").strip()[:20]
    rows = []
    for r in (out.get("rows") or [])[:600]:
        if not isinstance(r, dict):
            continue
        rows.append({"code": str(r.get("code") or "").strip()[:60], "name": str(r.get("name") or "").strip()[:160],
                     "category": str(r.get("category") or "").strip()[:60], "season": season,
                     "mrp": _num(r.get("mrp")), "purchase_price": _num(r.get("dp")), "selling_price": _num(r.get("retailer_price"))})
    return rows


def read_soa(data: bytes, mime: str, *, user_id: int) -> dict[str, Any]:
    out = _ask_gemini(data, mime, user_id, _PROMPT_SOA)
    rows = []
    for r in (out.get("rows") or [])[:800]:
        if isinstance(r, dict):
            rows.append({"date": str(r.get("date") or "")[:10], "doc_no": str(r.get("doc_no") or "").strip()[:40],
                         "narration": str(r.get("narration") or "").strip()[:160],
                         "debit": _num(r.get("debit")), "credit": _num(r.get("credit"))})
    return {"rows": rows, "opening_balance": _num(out.get("opening_balance")), "closing_balance": _num(out.get("closing_balance"))}

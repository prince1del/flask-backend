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
    "\"total\":0,\"expense_category\":\"\"}. "
    "charges = freight, packing, loading, labour etc. charged separately (amount before GST); free_qty = free units. "
    "party = the seller who issued the bill (not us). rate = price per unit BEFORE GST. "
    "tax_pct = GST % of that line (CGST+SGST together, or IGST). total = grand total in rupees. "
    "expense_category = one or two words if this is an expense like rent, electricity, petrol, "
    "telephone, transport, repairs, salary; else \"\". Use \"\" or 0 when not on the document. "
    "At most 40 lines."
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


def _ask_gemini(data: bytes, mime: str, user_id: int) -> dict[str, Any]:
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
            {"text": _PROMPT},
            {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}},
        ]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 2000, "responseMimeType": "application/json"},
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
        "expense_category_id": category["id"] if category else None,
        "expense_category": category["name"] if category else cat_name,
        "checks": notes,
    }


def read_bill(conn: sqlite3.Connection, ws: str, data: bytes, mime: str, *, user_id: int) -> dict[str, Any]:
    return match_bill(conn, ws, _ask_gemini(data, mime, user_id))


# ── Ask Nexora: get_hop_books ────────────────────────────────────────────────

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
        return {"accounts": [{"name": a["name"], "type": a["account_type"], "balance": a["current_balance"]} for a in accts],
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
        for kind, table in (("customer", "hop_customers"), ("vendor", "hop_vendors")):
            for r in conn.execute(f"SELECT id, company FROM {table} WHERE workspace_id=?", (ws,)).fetchall():
                sc = _similar(party_name, r[1] or "")
                if sc >= 0.6 and (best is None or sc > best[0]):
                    best = (sc, kind, int(r[0]))
        if not best:
            return {"error": f"No party like '{party_name}'."}
        stmt = hop_reports.party_statement(conn, ws, best[1], best[2], str(extra.get("from") or ""), str(extra.get("to") or ""))
        stmt["lines"] = stmt["lines"][-30:]
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
                "item_problems": [{"name": r["name"], "problems": r["checks"]} for r in rows if r.get("checks")][:15]}
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

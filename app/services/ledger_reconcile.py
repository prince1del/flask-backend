"""Check a distributor's party ledger against NEXORA and list every mismatch.

The ledger (SAP party GL Excel, or a PDF/photo read by Gemini) gives three
kinds of lines: invoices, payments and credit notes. Each is matched with what
NEXORA has for that distributor (all seasons):

* Invoice  ↔ CI by invoice number; amount compared with the CI gross.
* Payment  ↔ deposit by amount, date within 10 days (closest first).
* Credit note ↔ credit note by number, else by amount.
* SAP books cash discount ("3%CD …") and TDS ("TDS: …") as credit notes too;
  those are compared, as totals, with the CD / TDS NEXORA already takes off
  each CI — they are not user credit notes.

Only the stretch of time NEXORA has data for this distributor is compared
(older ledger lines are counted as skipped), unless a from-date is given.

Every line that has no partner, or whose amount differs, becomes a finding the
user can act on ("payment of ₹5 L on 4 Aug is in the ledger but not in NEXORA").
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any

_TOL = 2.0  # rupees: rounding differences are not a mismatch
_PAY_DAYS = 10


def _d(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    text = str(v or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _num(text: Any) -> str:
    """Invoice / CN number key: digits only when there are enough, else upper text."""
    raw = str(text or "").strip()
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) >= 5 else raw.upper()


def _inr(v: float) -> str:
    # Indian grouping: 12,34,567
    n = f"{abs(v):.0f}"
    if len(n) > 3:
        last3, rest = n[-3:], n[:-3]
        rest = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", rest)
        n = f"{rest},{last3}"
    return ("−₹" if v < 0 else "₹") + n


def _fmt(d: date | None) -> str:
    return d.strftime("%d %b %Y").lstrip("0") if d else "—"


# ── Ledger lines ─────────────────────────────────────────────────────────────

_CD_RE = re.compile(r"(\d+(\.\d+)?\s*%\s*CD\b|\bCASH\s+DISCOUNT\b|^\s*CD\b)", re.I)
_TDS_RE = re.compile(r"^\s*TDS\b|\bTDS\s*:", re.I)
_CI_NO_RE = re.compile(r"^\d{8,}$")


def credit_kind(text: str) -> str:
    """SAP 'Credit Note' lines: cash discount, TDS, or a real credit note."""
    if _TDS_RE.search(text or ""):
        return "tds"
    if _CD_RE.search(text or ""):
        return "cd"
    return "credit_note"


def lines_from_statement(statement: dict[str, Any]) -> list[dict[str, Any]]:
    """SAP GL parse (statement_of_account) → uniform lines.
    SAP keeps the CI number in Assignment (Reference is SAP's own doc number)."""
    kind_of = {"Sale Invoice": "invoice", "Payment Received": "payment", "Credit Note": "credit_note"}
    out = []
    for ln in statement.get("lines") or []:
        kind = kind_of.get(str(ln.get("type") or ""), "other")
        amt = abs(float(ln.get("signed") or 0))
        if amt < 0.01:
            continue
        ref = str(ln.get("reference") or "").strip()
        assign = str(ln.get("assignment") or "").strip()
        text = str(ln.get("text") or "").strip()
        if kind == "invoice" and _CI_NO_RE.match(assign):
            ref, assign = assign, ref
        if kind == "credit_note":
            kind = credit_kind(text)
        out.append({
            "kind": kind,
            "date": _d(ln.get("date")),
            "ref": ref,
            "so_ref": assign,
            "text": text,
            "amount": round(amt, 2),
            "signed": float(ln.get("signed") or 0),
        })
    return out


_PDF_PROMPT = (
    "This is a party ledger / account statement between a company and its distributor. "
    "List EVERY transaction line. Return ONLY JSON: "
    '{"party":"","closing_balance":0,"lines":[{"date":"YYYY-MM-DD","kind":"invoice|payment|credit_note|other",'
    '"ref":"","so_ref":"","text":"","debit":0,"credit":0}]}. '
    "kind: invoice = sale/tax invoice (debit), payment = receipt/bank/RTGS/NEFT/cheque (credit), "
    "cd = cash discount credit, tds = TDS credit, "
    "credit_note = other credit note/sales return/rate difference/scheme (credit), other = anything else "
    "(opening balance, journal). ref = invoice (CI) / CN / UTR number. so_ref = order/SO number if shown. "
    "Amounts are plain numbers in rupees. Do not invent lines."
)


def _gemini(parts: list[dict[str, Any]], *, user_id: int, feature: str, max_tokens: int,
            json_out: bool, timeout: int = 120) -> str:
    """One capped Gemini call (logged, counted in the daily AI limit). Returns the text."""
    from app.services import nexora_ai_agent as agent
    from app.services.dsr_notes_polish import gemini_api_key

    if agent.over_daily_limit(user_id, "ai"):
        raise ValueError("Daily AI limit reached for today.")
    key = gemini_api_key()
    if not key:
        raise ValueError("AI is not set up on the server.")
    cfg: dict[str, Any] = {"temperature": 0, "maxOutputTokens": max_tokens}
    if json_out:
        cfg["responseMimeType"] = "application/json"
    body = json.dumps({"contents": [{"role": "user", "parts": parts}], "generationConfig": cfg}).encode("utf-8")
    for model in agent.agent_gemini_models():
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            data=body, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise ValueError("AI could not read it right now.") from exc
        except Exception as exc:
            raise ValueError("AI could not read it right now.") from exc
        usage = out.get("usageMetadata") or {}
        agent._log_usage(
            user_id=int(user_id), feature=feature, model=model, rounds=1,
            prompt_tokens=int(usage.get("promptTokenCount") or 0),
            output_tokens=int(usage.get("candidatesTokenCount") or 0),
            total_tokens=int(usage.get("totalTokenCount") or 0), query=feature,
        )
        parts_out = ((out.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts_out if isinstance(p, dict))
    raise ValueError("AI could not read it right now.")


def _lines_from_ai_json(text: str) -> tuple[str, list[dict[str, Any]]]:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        parsed = json.loads(m.group(0)) if m else {}
    except ValueError:
        parsed = {}
    lines = []
    for ln in parsed.get("lines") or []:
        if not isinstance(ln, dict):
            continue
        try:
            debit = float(str(ln.get("debit") or 0).replace(",", ""))
            credit = float(str(ln.get("credit") or 0).replace(",", ""))
        except ValueError:
            continue
        amt = debit or credit
        if amt <= 0:
            continue
        kind = str(ln.get("kind") or "other")
        txt = str(ln.get("text") or "").strip()[:120]
        if kind == "credit_note":
            kind = credit_kind(txt)  # AI may call a CD/TDS line a credit note
        lines.append({
            "kind": kind if kind in ("invoice", "payment", "credit_note", "cd", "tds") else "other",
            "date": _d(ln.get("date")),
            "ref": str(ln.get("ref") or "").strip(),
            "so_ref": str(ln.get("so_ref") or "").strip(),
            "text": txt,
            "amount": round(amt, 2),
            "signed": debit - credit,
        })
    if not lines:
        raise ValueError("No transactions found in this file.")
    return str(parsed.get("party") or "").strip(), lines


def lines_from_document(data: bytes, mime: str, *, user_id: int) -> tuple[str, list[dict[str, Any]]]:
    """Read a PDF/photo ledger with Gemini (one call, capped). Returns (party, lines)."""
    if len(data) > 15 * 1024 * 1024:
        raise ValueError("File is too big (max 15 MB).")
    try:
        text = _gemini([{"text": _PDF_PROMPT},
                        {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}}],
                       user_id=user_id, feature="nexora_agent_ledger_read", max_tokens=12000, json_out=True)
    except ValueError as exc:
        raise ValueError(f"{exc} Upload the ledger as Excel if you can.") from exc
    return _lines_from_ai_json(text)


_MAX_SHEET_ROWS = 700


def lines_from_sheet(headers: list[str], rows: list[list[Any]], *, user_id: int) -> tuple[str, list[dict[str, Any]]]:
    """Any Excel layout NEXORA does not know: send the rows as text to Gemini.
    Newest rows are kept when the sheet is very long."""
    def cell(v: Any) -> str:
        if isinstance(v, (datetime, date)):
            return v.strftime("%Y-%m-%d")
        return str(v if v is not None else "").replace("|", "/").replace("\n", " ").strip()[:60]

    body_rows = [r for r in rows if any(cell(c) for c in r)][-_MAX_SHEET_ROWS:]
    table = "\n".join(" | ".join(cell(c) for c in r) for r in [headers] + body_rows)
    try:
        text = _gemini([{"text": _PDF_PROMPT + " The ledger is given as a table, one row per line:\n" + table}],
                       user_id=user_id, feature="nexora_agent_ledger_read", max_tokens=12000, json_out=True)
    except ValueError as exc:
        raise ValueError(f"This Excel layout is new and {str(exc).lower()}") from exc
    return _lines_from_ai_json(text)


def summarize(result: dict[str, Any], *, user_id: int) -> str | None:
    """Two or three plain sentences on what to fix first (one small AI call)."""
    findings = result.get("findings") or []
    if not findings:
        return None
    facts = {
        "distributor": result.get("distributor_name"),
        "period": [result.get("period_from"), result.get("period_to")],
        "totals_ledger_vs_nexora": result.get("compare"),
        "mismatches": [{"title": f["title"], "detail": f["detail"], "likely": f.get("likely")}
                       for f in findings[:15]],
        "more": max(0, len(findings) - 15),
    }
    prompt = ("You check a distributor's company ledger against our app NEXORA. In at most 3 short "
              "sentences of plain English, say what is wrong and what to fix first (biggest money first). "
              "Use ₹ with Indian lakh style (e.g. ₹2.97 L). No markdown, no lists.\n" + json.dumps(facts, default=str))
    try:
        text = _gemini([{"text": prompt}], user_id=user_id, feature="nexora_agent_ledger_summary",
                       max_tokens=220, json_out=False, timeout=30)
    except ValueError:
        return None
    return re.sub(r"[*#`]", "", text).strip()[:600] or None


# ── Which category / season does a ledger line belong to? ────────────────────

_CAT_WORDS = (
    ("Bath", re.compile(r"\b(bath|towel)", re.I)),
    ("Pillow", re.compile(r"\bpillow", re.I)),
    ("TOB", re.compile(r"\b(tob|top of bed|comforter|quilt|dohar|blanket)", re.I)),
    ("Bed", re.compile(r"\b(bed|bedsheet|sheet)", re.I)),
)
_RFA_RE = re.compile(r"RFA\s*[-:]?\s*0*(\d{2,})", re.I)
_CI_LIST_RE = re.compile(r"CI\s*[-:]?\s*(\d{8,}(?:\s*/\s*\d{1,})*)", re.I)


def _category_in(text: str) -> str | None:
    for cat, rx in _CAT_WORDS:
        if rx.search(text or ""):
            return cat
    return None


def _expand_ci_list(raw: str) -> list[str]:
    """'1400009209/10/1/2/27/8' -> 1400009209, …210, …211, …212, …227, …228."""
    parts = [x.strip() for x in raw.split("/") if x.strip()]
    if not parts:
        return []
    out = [parts[0]]
    for t in parts[1:]:
        prev = out[-1]  # each short part replaces the last digits of the one before
        out.append(prev[: len(prev) - len(t)] + t if len(t) < len(prev) else t)
    return out


def ledger_hints(lines: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    """(RFA -> category, CI number -> RFA) learned from the ledger's own text."""
    rfa_cat: dict[str, str] = {}
    ci_rfa: dict[str, str] = {}
    for ln in lines:
        blob = f"{ln.get('so_ref') or ''} {ln.get('text') or ''}"
        m = _RFA_RE.search(blob)
        if not m:
            continue
        rfa = m.group(1).lstrip("0") or "0"
        cat = _category_in(blob)
        if cat:
            rfa_cat.setdefault(rfa, cat)
        for lst in _CI_LIST_RE.findall(blob):
            for ci in _expand_ci_list(lst):
                ci_rfa.setdefault(ci, rfa)
    return rfa_cat, ci_rfa


# ── Which distributor? ───────────────────────────────────────────────────────

def _words(name: str) -> set[str]:
    stop = {"pvt", "ltd", "p", "private", "limited", "and", "co", "the", "m/s", "ms", "&"}
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if w not in stop and len(w) > 1}


def match_distributor(party: str, distributors: list[dict[str, Any]]) -> dict[str, Any] | None:
    want = _words(party)
    if not want:
        return None
    best, score = None, 0.0
    for d in distributors:
        have = _words(str(d.get("distributor_name") or ""))
        if not have:
            continue
        s = len(want & have) / max(len(want), len(have))
        if s > score:
            best, score = d, s
    return best if score >= 0.5 else None


# ── Compare ──────────────────────────────────────────────────────────────────

def reconcile(lines: list[dict[str, Any]], dist: dict[str, Any], since: date | None = None) -> dict[str, Any]:
    """Findings for one distributor: ledger lines vs NEXORA (build_ledger row).
    [since]: compare from this date; default = the first date NEXORA has."""
    cis: list[dict[str, Any]] = []
    deposits: list[dict[str, Any]] = []
    cns: list[dict[str, Any]] = []
    for c in dist.get("categories") or []:
        label = " · ".join(x for x in (c.get("category"), c.get("season")) if x)
        cis += [{**ci, "_where": label} for ci in c.get("cis") or []]
        deposits += [{**p, "_where": label} for p in c.get("deposits") or []]
        cns += [{**n, "_where": label} for n in c.get("credit_notes") or []]
    cis += [{**ci, "_where": "not linked to an SO"} for ci in dist.get("unlinked_cis") or []]

    nx_dates = [x for x in (
        [_d(ci.get("invoice_date")) for ci in cis]
        + [_d(p.get("payment_date")) for p in deposits]
        + [_d(n.get("cn_date")) for n in cns]) if x]
    # A payment often lands a few days before the first CI / entry NEXORA has.
    start = since or (min(nx_dates) - timedelta(days=_PAY_DAYS) if nx_dates else None)
    all_lines = lines
    lines = [ln for ln in all_lines if not start or not ln.get("date") or ln["date"] >= start]
    skipped = len(all_lines) - len(lines)
    dated = [ln["date"] for ln in lines if ln.get("date")]
    p_from, p_to = (min(dated), max(dated)) if dated else (None, None)
    if start and p_from and start < p_from:
        p_from = start

    def in_period(d: date | None) -> bool:
        return bool(d and p_from and p_to and p_from <= d <= p_to)

    findings: list[dict[str, Any]] = []
    rfa_cat, ci_rfa = ledger_hints(all_lines)
    seasons_of: dict[str, list[str]] = {}
    for c in dist.get("categories") or []:
        if c.get("category") and c.get("season"):
            seasons_of.setdefault(c["category"], []).append(c["season"])

    def likely(ln: dict[str, Any]) -> str | None:
        """'Bed · AW26 (RFA 0377)' from the line's own words, or via its RFA."""
        blob = f"{ln.get('so_ref') or ''} {ln.get('text') or ''}"
        m = _RFA_RE.search(blob)
        rfa = (m.group(1).lstrip("0") or "0") if m else ci_rfa.get(_num(ln.get("ref")))
        cat = _category_in(blob) or (rfa_cat.get(rfa) if rfa else None)
        if not cat:
            return None
        # newest season last: AW26 after SS26
        seasons = sorted(set(seasons_of.get(cat, [])), key=lambda x: (x[-2:], 1 if x.upper().startswith("AW") else 0))
        label = cat + (f" · {seasons[-1]}" if seasons else "")
        return label + (f" (RFA {rfa})" if rfa else "")

    def add(kind: str, level: str, title: str, detail: str, action: str, amount: float = 0.0,
            ref: str = "", tracking_id: int | None = None, line: dict[str, Any] | None = None) -> None:
        hint = likely(line) if line else None
        findings.append({"kind": kind, "level": level, "title": title,
                         "detail": detail + (f" · Likely {hint}" if hint else ""),
                         "likely": hint, "action": action, "amount": round(amount, 2), "ref": ref,
                         "tracking_id": tracking_id})

    # Invoices
    ci_by_no: dict[str, dict[str, Any]] = {}
    for ci in cis:
        key = _num(ci.get("invoice_no"))
        if key:
            ci_by_no.setdefault(key, ci)
    seen_ci: set[str] = set()
    led_inv = [ln for ln in lines if ln["kind"] == "invoice"]
    for ln in led_inv:
        key = _num(ln["ref"])
        ci = ci_by_no.get(key) if key else None
        if ci is None:
            add("invoice", "alert", f"Invoice {ln['ref'] or '?'} is missing in NEXORA",
                f"Ledger: {_inr(ln['amount'])} on {_fmt(ln['date'])}"
                + (f" · SO {ln['so_ref']}" if ln["so_ref"] else ""),
                "Upload this CI in Order Desk → Commercial Invoices.", ln["amount"], ln["ref"], line=ln)
            continue
        seen_ci.add(key)
        gross = float(ci.get("gross") or 0)
        if abs(gross - ln["amount"]) > _TOL:
            add("invoice", "warn", f"Invoice {ln['ref']}: amount differs",
                f"Ledger {_inr(ln['amount'])} vs NEXORA {_inr(gross)} (difference {_inr(ln['amount'] - gross)})",
                "Open the CI and check the total; re-upload if it was read wrong.",
                ln["amount"] - gross, ln["ref"], ci.get("tracking_id"), line=ln)
    for key, ci in ci_by_no.items():
        if key in seen_ci or not in_period(_d(ci.get("invoice_date"))):
            continue
        add("invoice", "warn", f"CI {ci.get('invoice_no')} is in NEXORA but not in the ledger",
            f"NEXORA: {_inr(float(ci.get('gross') or 0))} on {_fmt(_d(ci.get('invoice_date')))} · {ci['_where']}",
            "Check if the company booked it; it may be cancelled or for another party.",
            float(ci.get("gross") or 0), str(ci.get("invoice_no") or ""), ci.get("tracking_id"))

    # Payments: closest date with the same amount, within _PAY_DAYS
    free = list(range(len(deposits)))
    for ln in [x for x in lines if x["kind"] == "payment"]:
        best, gap = None, 10**6
        for i in free:
            p = deposits[i]
            if abs(float(p.get("amount") or 0) - ln["amount"]) > _TOL:
                continue
            pd, ld = _d(p.get("payment_date")), ln["date"]
            g = abs((pd - ld).days) if pd and ld else _PAY_DAYS
            if g <= _PAY_DAYS and g < gap:
                best, gap = i, g
        if best is not None:
            free.remove(best)
            continue
        add("payment", "alert", f"Payment {_inr(ln['amount'])} on {_fmt(ln['date'])} is missing in NEXORA",
            "In the ledger" + (f" · ref {ln['ref']}" if ln["ref"] else "")
            + (f" · {ln['text']}" if ln["text"] else ""),
            "Add it in Distributors Payment Status → Payments for the right season and category.",
            ln["amount"], ln["ref"], line=ln)
    for i in free:
        p = deposits[i]
        pd = _d(p.get("payment_date"))
        if not in_period(pd):
            continue
        add("payment", "warn", f"Payment {_inr(float(p.get('amount') or 0))} on {_fmt(pd)} is not in the ledger",
            f"In NEXORA · {p['_where']}" + (f" · {p.get('note')}" if p.get("note") else ""),
            "Check the amount and date; the company may have booked it differently.",
            float(p.get("amount") or 0))

    # Credit notes: by number, else by amount
    cn_free = list(range(len(cns)))
    for ln in [x for x in lines if x["kind"] == "credit_note"]:
        hit = None
        key = _num(ln["ref"])
        for i in cn_free:
            if key and _num(cns[i].get("cn_no")) == key:
                hit = i
                break
        if hit is None:
            for i in cn_free:
                if abs(float(cns[i].get("amount") or 0) - ln["amount"]) <= _TOL:
                    hit = i
                    break
        if hit is not None:
            n = cns[hit]
            cn_free.remove(hit)
            if abs(float(n.get("amount") or 0) - ln["amount"]) > _TOL:
                add("credit_note", "warn", f"Credit note {ln['ref'] or n.get('cn_no') or ''}: amount differs",
                    f"Ledger {_inr(ln['amount'])} vs NEXORA {_inr(float(n.get('amount') or 0))}",
                    "Fix the credit note amount in Hisaab.", ln["amount"] - float(n.get("amount") or 0), ln["ref"])
            continue
        add("credit_note", "alert",
            f"Credit note {ln['ref'] or ''} {_inr(ln['amount'])} is missing in NEXORA".replace("  ", " "),
            f"Ledger: {_fmt(ln['date'])}" + (f" · {ln['text']}" if ln["text"] else ""),
            "Add it in Hisaab → + Add credit note for the right category.", ln["amount"], ln["ref"], line=ln)
    for i in cn_free:
        n = cns[i]
        if not in_period(_d(n.get("cn_date"))):
            continue
        add("credit_note", "warn", f"Credit note {n.get('cn_no') or ''} {_inr(float(n.get('amount') or 0))} is not in the ledger".replace("  ", " "),
            f"In NEXORA · {n['_where']}", "Check with the company if it was issued.", float(n.get("amount") or 0))

    led = {k: round(sum(x["amount"] for x in lines if x["kind"] == k), 2)
           for k in ("invoice", "payment", "credit_note", "cd", "tds")}
    period_cis = [ci for ci in cis if in_period(_d(ci.get("invoice_date")))]
    nx_cd = round(sum(float(ci.get("cd") or 0) for ci in period_cis), 2)
    nx_tds = round(sum(float(ci.get("tds") or 0) for ci in period_cis), 2)
    for label, lv, nv, what in (("Cash discount (CD)", led["cd"], nx_cd, "CD %"),
                                ("TDS", led["tds"], nx_tds, "TDS")):
        diff = lv - nv
        if (lv or nv) and abs(diff) > max(50.0, 0.01 * max(lv, nv)):
            add("cd" if what == "CD %" else "tds", "warn",
                f"{label} differs: ledger {_inr(lv)}, NEXORA {_inr(nv)}",
                f"Difference {_inr(diff)} for the invoices in this period.",
                f"Check the {what} set for this distributor in Payment Status, or CD/TDS not yet given by the company.",
                diff)
    nx_inv = round(sum(float(ci.get("gross") or 0) for ci in cis if in_period(_d(ci.get("invoice_date")))), 2)
    nx_pay = round(sum(float(p.get("amount") or 0) for p in deposits if in_period(_d(p.get("payment_date")))), 2)
    nx_cn = round(sum(float(n.get("amount") or 0) for n in cns if in_period(_d(n.get("cn_date")))), 2)
    order = {"alert": 0, "warn": 1}
    findings.sort(key=lambda f: (order.get(f["level"], 2), -abs(f["amount"])))
    return {
        "distributor_id": dist.get("distributor_id"),
        "distributor_name": dist.get("distributor_name"),
        "period_from": p_from.isoformat() if p_from else None,
        "period_to": p_to.isoformat() if p_to else None,
        "compare": [
            {"label": "Invoices", "ledger": led["invoice"], "nexora": nx_inv},
            {"label": "Payments", "ledger": led["payment"], "nexora": nx_pay},
            {"label": "Credit notes", "ledger": led["credit_note"], "nexora": nx_cn},
            {"label": "Cash discount (CD)", "ledger": led["cd"], "nexora": nx_cd},
            {"label": "TDS", "ledger": led["tds"], "nexora": nx_tds},
        ],
        "line_count": len(lines),
        "skipped_older": skipped,
        "compared_from": start.isoformat() if start else None,
        "findings": findings,
        "all_clear": not findings,
    }

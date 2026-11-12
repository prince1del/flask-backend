"""SO mail import: Bombay Dyeing "RFA … for Cash payment" mails → Order Desk + Payment.

Flow (nothing is saved to orders/payments until a person confirms):

1. scan(): read matching Gmail messages, parse the mail table
   (so_mail_parser), analyze the attached SO PDFs with the same Order Desk
   analyzer, identify the distributor and candidate Filled Order(s), compare
   mail vs PDF per SO, and stage one `so_mail_imports` row per mail
   (status review / already_imported / error).
2. confirm(): runs Order Desk's own FO↔SO match for each chosen Filled Order
   (the /so-pack/match-filled-order view, same code path as the UI), then
   writes Cash Discount %, TDS 194Q and the payment due date for that
   distributor + season + category.
3. reject(): marks the mail as ignored.

Re-scanning never duplicates: a mail is staged once (message_id), and SOs
already present in Order Desk are reported as already imported.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.services import fo_so_match_db as matchdb
from app.services import so_mail_parser as parser

logger = logging.getLogger(__name__)

MAIL_WINDOW_DAYS = 120
SO_MAIL_QUERY = (
    f'newer_than:{MAIL_WINDOW_DAYS}d has:attachment filename:pdf '
    'subject:("for Cash payment" OR "Cash payment" OR RFA)'
)

# Mail vs PDF tolerance (rounding in the mail table).
_MONEY_TOL = 2.0
_QTY_TOL = 0.5

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS so_mail_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    workspace_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    subject TEXT,
    email_date TEXT,
    status TEXT NOT NULL,
    category TEXT,
    distributor_id INTEGER,
    distributor_name TEXT,
    parsed_json TEXT,
    pack_json TEXT,
    compare_json TEXT,
    proposal_json TEXT,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, workspace_id, message_id)
);
CREATE TABLE IF NOT EXISTS distributor_payment_due_dates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    distributor_id INTEGER NOT NULL,
    season TEXT NOT NULL,
    category TEXT NOT NULL,
    due_date TEXT NOT NULL,
    mail_total REAL,
    source TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, distributor_id, season, category)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


# ---------------------------------------------------------------- Gmail


def _decode_part(part: dict[str, Any], mime: str) -> str:
    if part.get("mimeType") == mime and (part.get("body") or {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
    for sub in part.get("parts") or []:
        text = _decode_part(sub, mime)
        if text:
            return text
    return ""


def fetch_mail(service, message_id: str) -> dict[str, Any]:
    from app.services.gmail_ci_sync import _extract_message_pdfs

    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    payload = msg.get("payload") or {}
    subject, email_date, pdfs = _extract_message_pdfs(service, message_id)
    return {
        "subject": subject,
        "email_date": email_date,
        "html": _decode_part(payload, "text/html"),
        "plain": _decode_part(payload, "text/plain"),
        "pdfs": pdfs,
    }


# ------------------------------------------------------- distributor / FO


def _norm(text: str) -> str:
    t = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    t = re.sub(r"\b(m s|ms|pvt|ltd|private|limited|co|and|the)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def match_distributor(
    conn: sqlite3.Connection, workspace_id: str, buyer_names: list[str]
) -> dict[str, Any] | None:
    """Best master distributor for the SO buyer name(s) (firm / nick / name)."""
    from rapidfuzz import fuzz

    try:
        rows = conn.execute(
            "SELECT id, firm_name, firm_nick_name, name FROM master_distributors "
            "WHERE workspace_id = ? OR workspace_id IS NULL",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    best: tuple[float, Any] | None = None
    for buyer in {b for b in buyer_names if b}:
        nb = _norm(buyer.split(",")[0])
        if not nb:
            continue
        for did, firm, nick, name in rows:
            for cand in (firm, nick, name):
                nc = _norm(cand or "")
                if not nc:
                    continue
                score = fuzz.token_set_ratio(nb, nc)
                if best is None or score > best[0]:
                    best = (score, {"id": int(did), "name": firm or nick or name, "score": score})
    if best and best[0] >= 80:
        return best[1]
    return None


def candidate_filled_orders(
    conn: sqlite3.Connection, user_id: int, distributor_id: int, category: str | None
) -> list[dict[str, Any]]:
    import filled_orders_db as fodb

    orders = fodb.list_filled_orders(conn, user_id, distributor_id=distributor_id)
    if category:
        same = [o for o in orders if str(o.get("category") or "").lower() == category.lower()]
        if same:
            orders = same
    return orders


def _already_imported(conn: sqlite3.Connection, user_id: int, so_numbers: list[str]) -> set[str]:
    if not so_numbers:
        return set()
    try:
        q = ",".join("?" for _ in so_numbers)
        rows = conn.execute(
            f"SELECT so_number FROM fo_so_match_so_index WHERE user_id = ? AND so_number IN ({q})",
            [user_id, *so_numbers],
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {r[0] for r in rows}


# ------------------------------------------------------------ compare


def compare_mail_vs_pack(parsed: dict[str, Any], pack: dict[str, Any] | None) -> dict[str, Any]:
    """Per-SO and total comparison of the mail table against the SO PDFs."""
    pdf_by_so = {
        str(s.get("so_number")): s for s in ((pack or {}).get("so_summary") or []) if s.get("so_number")
    }
    rows = []
    diffs = []
    for r in parsed.get("so_rows") or []:
        so = r["so_number"]
        p = pdf_by_so.get(so)
        row = {
            "so_number": so,
            "mail_qty": r.get("qty"),
            "mail_basic": r.get("basic"),
            "mail_gross": r.get("gross"),
            "pdf_qty": p.get("total_qty") if p else None,
            "pdf_net": p.get("net_amount") if p else None,
            "pdf_total": p.get("total_amount") if p else None,
        }
        if p is None:
            row["status"] = "missing_pdf"
            diffs.append(f"SO {so}: in mail, no PDF attached")
        elif abs((r.get("basic") or 0) - (p.get("net_amount") or 0)) > _MONEY_TOL or abs(
            (r.get("qty") or 0) - (p.get("total_qty") or 0)
        ) > _QTY_TOL:
            row["status"] = "mismatch"
            diffs.append(
                f"SO {so}: mail {r.get('qty'):g} pcs / {r.get('basic'):,.2f} vs PDF "
                f"{p.get('total_qty'):g} pcs / {p.get('net_amount'):,.2f}"
            )
        else:
            row["status"] = "match"
        rows.append(row)
    mail_sos = {r["so_number"] for r in parsed.get("so_rows") or []}
    for so in sorted(set(pdf_by_so) - mail_sos):
        diffs.append(f"SO {so}: PDF attached but not listed in the mail table")
        rows.append({"so_number": so, "status": "extra_pdf", "pdf_qty": pdf_by_so[so].get("total_qty"),
                     "pdf_net": pdf_by_so[so].get("net_amount")})
    s = parsed.get("summary") or {}
    totals = {
        "mail_qty": s.get("qty"),
        "mail_basic": s.get("basic"),
        "mail_gross": s.get("gross"),
        "pdf_qty": (pack or {}).get("meta", {}).get("total_qty"),
        "pdf_net": (pack or {}).get("meta", {}).get("net_amount"),
        "pdf_total": (pack or {}).get("meta", {}).get("total_amount"),
    }
    return {"rows": rows, "totals": totals, "diffs": diffs, "all_match": not diffs}


# -------------------------------------------------------------- checks


AUTO_DISTRIBUTOR_SCORE = 85


def auto_answers(questions: list[dict[str, Any]]) -> dict[str, str] | None:
    """Answers NEXORA can give on its own, or None when any question needs the person."""
    out: dict[str, str] = {}
    for q in questions or []:
        if not q.get("auto"):
            return None
        out[q["key"]] = q["auto"]
    return out


def evaluate_checks(
    parsed: dict[str, Any],
    compare: dict[str, Any] | None,
    proposal: dict[str, Any],
    current_payment: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Five checks; a mail auto-saves only when every one passes.

    Each failing check becomes a plain question with answer options, so the
    person never has to re-add numbers — only decide what only they know.
    """
    checks: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    summary = parsed.get("summary") or {}

    # 1) Mail table vs SO PDFs
    if compare and compare.get("all_match"):
        checks.append({"key": "mail_vs_pdf", "ok": True, "text": "Mail matches the SO PDFs"})
    else:
        diffs = (compare or {}).get("diffs") or ["SO PDFs could not be compared"]
        checks.append({"key": "mail_vs_pdf", "ok": False, "text": "; ".join(diffs[:3])})
        questions.append({
            "key": "mail_vs_pdf",
            "text": "Mail and SO PDFs differ: " + "; ".join(diffs[:3]) +
                    ". Order Desk uses the PDF. Save with the PDF figures?",
            "options": [{"value": "pdf", "label": "Save with PDF figures"},
                        {"value": "ignore", "label": "Ignore this mail"}],
            "auto": "pdf",
            "auto_reason": "Mail and SO PDF differ — used the SO PDF figures (Order Desk always uses the PDF)",
        })

    # 2) Distributor
    dist = proposal.get("distributor") or {}
    if dist and float(dist.get("score") or 0) >= 90:
        checks.append({"key": "distributor", "ok": True, "text": f"Distributor: {dist.get('name')}"})
    else:
        checks.append({"key": "distributor", "ok": False,
                       "text": "Distributor not certain" + (f" (best guess {dist.get('name')})" if dist else "")})
        good_guess = bool(dist) and float(dist.get("score") or 0) >= AUTO_DISTRIBUTOR_SCORE
        questions.append({"key": "distributor",
                          "text": "Which distributor is this mail for?"
                                  + (f" Best guess: {dist.get('name')}." if dist else ""),
                          "options": [{"value": "pick", "label": "Pick distributor"}],
                          "auto": "pick" if good_guess else None,
                          "auto_reason": f"Distributor taken as {dist.get('name')} (name match "
                                         f"{float(dist.get('score') or 0):.0f}%)" if good_guess else None})

    # 3) Filled Order present and quantities line up (±5%)
    cands = proposal.get("filled_order_candidates") or []
    fo_qty = sum(float(c.get("total_piece_qty") or 0) for c in cands)
    mail_qty = float(summary.get("qty") or 0)
    if cands and mail_qty and fo_qty and abs(fo_qty - mail_qty) <= 0.05 * mail_qty:
        checks.append({"key": "filled_order", "ok": True,
                       "text": f"Filled Order found ({fo_qty:,.0f} pcs vs mail {mail_qty:,.0f})"})
    elif cands:
        checks.append({"key": "filled_order", "ok": False,
                       "text": f"Filled Order qty {fo_qty:,.0f} vs mail {mail_qty:,.0f}"})
        questions.append({"key": "filled_order",
                          "text": f"Order sheet has {fo_qty:,.0f} pcs but the mail has {mail_qty:,.0f}. "
                                  "Match the SOs against it anyway?",
                          "options": [{"value": "match", "label": "Match anyway"},
                                      {"value": "ignore", "label": "Ignore this mail"}],
                          "auto": "match" if mail_qty <= fo_qty * 1.05 else None,
                          "auto_reason": f"Matched with the order sheet ({fo_qty:,.0f} pcs ordered, "
                                         f"{mail_qty:,.0f} pcs in this mail)"
                                         if mail_qty <= fo_qty * 1.05 else None})
    else:
        checks.append({"key": "filled_order", "ok": False, "text": "No Filled Order for this category"})
        questions.append({"key": "filled_order",
                          "text": "No order sheet for this distributor and category in NEXORA. "
                                  "Upload it in Order Desk, then check mails again.",
                          "options": [{"value": "later", "label": "OK, later"}]})

    # 4) CD / TDS vs what Payment already uses
    mail_cd = proposal.get("cd_percent")
    mail_tds = bool(proposal.get("tds_applicable"))
    if not current_payment:
        checks.append({"key": "payment", "ok": True, "text": "CD/TDS set for the first time from the mail"})
    else:
        cur_cd = current_payment.get("cd_percent")
        cur_tds = bool(current_payment.get("tds_applicable"))
        cd_same = cur_cd is None or mail_cd is None or abs(float(cur_cd) - float(mail_cd)) < 0.01 or float(cur_cd or 0) == 0
        if cd_same and cur_tds == mail_tds:
            checks.append({"key": "payment", "ok": True, "text": "CD/TDS same as Payment"})
        else:
            tds_blank = bool(summary.get("tds_dash"))
            if cd_same and tds_blank:
                auto_pay = "keep"
                auto_pay_reason = (f"Mail leaves TDS blank — kept TDS {'yes' if cur_tds else 'no'} "
                                   "as in Payment")
            else:
                auto_pay = "mail"
                auto_pay_reason = (f"CD/TDS set from the Bombay Dyeing mail: CD {mail_cd}% / TDS "
                                   f"{'yes' if mail_tds else 'no'} (was CD {cur_cd}% / TDS "
                                   f"{'yes' if cur_tds else 'no'})")
            checks.append({"key": "payment", "ok": False,
                           "text": f"Payment has CD {cur_cd}% / TDS {'yes' if cur_tds else 'no'}; "
                                   f"mail says CD {mail_cd}% / TDS {'yes' if mail_tds else 'no'}"})
            questions.append({
                "key": "payment",
                "text": f"Payment now uses CD {cur_cd}% and TDS {'yes' if cur_tds else 'no'}, "
                        f"the mail says CD {mail_cd}% and TDS {'yes' if mail_tds else 'no'}. Which to keep?",
                "options": [{"value": "keep", "label": "Keep current"},
                            {"value": "mail", "label": "Use the mail"}],
                "auto": auto_pay,
                "auto_reason": auto_pay_reason,
            })

    # 5) Not already in Order Desk
    already = proposal.get("already_imported_sos") or []
    if not already:
        checks.append({"key": "duplicate", "ok": True, "text": "SOs are new"})
    else:
        total = len(parsed.get("so_rows") or [])
        if len(already) >= total:
            checks.append({"key": "duplicate", "ok": True,
                           "text": "SOs already in Order Desk — only CD/TDS/due date to apply"})
        else:
            checks.append({"key": "duplicate", "ok": False,
                           "text": f"{len(already)} of {total} SOs already in Order Desk"})
        if len(already) < total:
            questions.append({"key": "duplicate",
                              "text": f"{len(already)} of these SOs are already in Order Desk. "
                                      "Match the full pack again (Order Desk will ask to replace)?",
                              "options": [{"value": "match", "label": "Match again"},
                                          {"value": "ignore", "label": "Ignore this mail"}]})
    return checks, questions


# ------------------------------------------------------------- staging


def _row_dict(cur: sqlite3.Cursor, row: tuple) -> dict[str, Any]:
    d = {c[0]: v for c, v in zip(cur.description, row)}
    for key in ("parsed_json", "compare_json", "proposal_json", "result_json"):
        raw = d.pop(key, None)
        d[key.replace("_json", "")] = json.loads(raw) if raw else None
    d.pop("pack_json", None)
    return d


def list_imports(conn: sqlite3.Connection, user_id: int, workspace_id: str,
                 status: str | None = None) -> list[dict[str, Any]]:
    ensure_schema(conn)
    sql = ("SELECT id, user_id, workspace_id, message_id, subject, email_date, status, category, "
           "distributor_id, distributor_name, parsed_json, pack_json, compare_json, proposal_json, "
           "result_json, error, created_at, updated_at FROM so_mail_imports "
           "WHERE user_id = ? AND workspace_id = ?")
    params: list[Any] = [user_id, workspace_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY COALESCE(email_date, created_at) DESC, id DESC"
    cur = conn.execute(sql, params)
    return [_row_dict(cur, r) for r in cur.fetchall()]


def refresh_checks(items: list[dict[str, Any]], existing_payment=None) -> list[dict[str, Any]]:
    """Recompute checks/questions for open items at read time.

    Keeps answers current (e.g. CD/TDS changed in Payment since staging) and
    gives mails staged before the checks existed the same question cards.
    Nothing is written back.
    """
    for item in items:
        if item.get("status") not in ("review", "already_imported"):
            continue
        proposal = item.get("proposal")
        parsed = item.get("parsed")
        if not isinstance(proposal, dict) or not isinstance(parsed, dict):
            continue
        dist = proposal.get("distributor") or {}
        current = None
        if existing_payment is not None and dist.get("id") and proposal.get("season") and proposal.get("category"):
            try:
                current = existing_payment(dist["id"], proposal["season"], proposal["category"])
            except Exception:
                current = None
        try:
            checks, questions = evaluate_checks(parsed, item.get("compare"), proposal, current)
        except Exception:
            continue
        proposal["checks"] = checks
        proposal["questions"] = questions
        proposal["auto_ok"] = auto_answers(questions) is not None
    return items


def get_import(conn: sqlite3.Connection, user_id: int, workspace_id: str, import_id: int,
               *, with_pack: bool = False) -> dict[str, Any] | None:
    ensure_schema(conn)
    cur = conn.execute(
        "SELECT id, user_id, workspace_id, message_id, subject, email_date, status, category, "
        "distributor_id, distributor_name, parsed_json, pack_json, compare_json, proposal_json, "
        "result_json, error, created_at, updated_at FROM so_mail_imports "
        "WHERE id = ? AND user_id = ? AND workspace_id = ?",
        (import_id, user_id, workspace_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    pack_raw = row[11]
    d = _row_dict(cur, row)
    if with_pack:
        d["pack"] = json.loads(pack_raw) if pack_raw else None
    return d


def stage_mail(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    workspace_id: str,
    message_id: str,
    mail: dict[str, Any],
    analyze_pdfs=None,
    existing_payment=None,
) -> dict[str, Any]:
    """Parse + analyze + compare one mail and store it for review (idempotent).

    existing_payment(distributor_id, season, category) -> {"cd_percent",
    "tds_applicable"} | None lets the checks compare the mail's CD/TDS with
    what Payment already uses for that slot.
    """
    ensure_schema(conn)
    existing = conn.execute(
        "SELECT id, status FROM so_mail_imports WHERE user_id=? AND workspace_id=? AND message_id=?",
        (user_id, workspace_id, message_id),
    ).fetchone()
    if existing:
        return {"id": existing[0], "status": existing[1], "new": False}

    parsed = parser.parse_so_mail(mail.get("subject") or "", mail.get("html") or "", mail.get("plain") or "")
    so_numbers = [r["so_number"] for r in parsed.get("so_rows") or []]
    status = "review"
    error = None
    pack = None
    if not so_numbers:
        status, error = "error", "No SO table found in the mail"
    else:
        pdfs = [(fn, data) for fn, data in (mail.get("pdfs") or []) if fn.lower().endswith(".pdf")]
        if not pdfs:
            status, error = "error", "No SO PDFs attached"
        else:
            try:
                if analyze_pdfs is None:
                    from app.services.so_pack_consolidate import analyze_so_pack_pdfs as analyze_pdfs
                pack = analyze_pdfs(pdfs, f"Mail: {mail.get('subject') or message_id}"[:120])
            except Exception as exc:  # unreadable PDFs etc.
                logger.exception("SO mail analyze failed for %s", message_id)
                status, error = "error", f"Could not read the SO PDFs: {exc}"

    compare = compare_mail_vs_pack(parsed, pack) if pack else None
    buyers = [s.get("buyer_name") or "" for s in ((pack or {}).get("so_summary") or [])]
    buyers += [r.get("party") or "" for r in parsed.get("so_rows") or []]
    dist = match_distributor(conn, workspace_id, buyers) if status != "error" else None
    candidates: list[dict[str, Any]] = []
    if dist:
        candidates = [
            {"id": o["id"], "category": o.get("category"), "season": o.get("season"),
             "source_filename": o.get("source_filename"), "total_piece_qty": o.get("total_piece_qty")}
            for o in candidate_filled_orders(conn, user_id, dist["id"], parsed.get("category"))
        ]
    already = sorted(_already_imported(conn, user_id, so_numbers))
    if status == "review" and so_numbers and len(already) == len(set(so_numbers)):
        status = "already_imported"
    summary = parsed.get("summary") or {}
    proposal = {
        "distributor": dist,
        "filled_order_candidates": candidates,
        # Default: every candidate of this category (e.g. Bernina SPL + regular towel orders).
        "filled_order_ids": [c["id"] for c in candidates],
        "season": candidates[0]["season"] if candidates else None,
        "category": parsed.get("category"),
        "cd_percent": summary.get("cd_percent"),
        "tds_applicable": bool(summary.get("tds_applied")),
        "tds_rate": parsed.get("tds_rate"),
        "due_date": parsed.get("due_date"),
        "mail_total_payable": summary.get("total"),
        "already_imported_sos": already,
    }
    if status == "review" and not dist:
        error = "Distributor not recognised — pick it before confirming"
    if status == "review" and dist and not candidates:
        error = "No saved Filled Order for this distributor/category — upload it in Order Desk first"
    if status in ("review", "already_imported"):
        current = None
        if existing_payment is not None and dist and proposal["season"] and proposal["category"]:
            try:
                current = existing_payment(dist["id"], proposal["season"], proposal["category"])
            except Exception:
                current = None
        checks, questions = evaluate_checks(parsed, compare, proposal, current)
        proposal["checks"] = checks
        proposal["questions"] = questions
        proposal["auto_ok"] = auto_answers(questions) is not None

    now = _now()
    cur = conn.execute(
        """INSERT INTO so_mail_imports
           (user_id, workspace_id, message_id, subject, email_date, status, category,
            distributor_id, distributor_name, parsed_json, pack_json, compare_json,
            proposal_json, error, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, workspace_id, message_id, mail.get("subject"), mail.get("email_date"), status,
         parsed.get("category"), dist["id"] if dist else None, dist["name"] if dist else None,
         json.dumps(parsed), json.dumps(pack) if pack else None,
         json.dumps(compare) if compare else None, json.dumps(proposal), error, now, now),
    )
    conn.commit()
    return {"id": cur.lastrowid, "status": status, "new": True}


def scan_mailbox(user_id: int, workspace_id: str, max_messages: int = 50,
                 auto_save: bool = True) -> dict[str, Any]:
    """Stage new SO mails from the connected Gmail inbox; auto-save the ones
    whose five checks all pass (needs a request context carrying the user)."""
    from app.routes.data import _db_path
    from app.services.gmail_ci_sync import build_gmail_service
    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(_db_path())
    account = db.get_storage_account(user_id=user_id, provider_type="gmail", workspace_id=workspace_id)
    if not account or account.get("sync_status") != "connected" or not account.get("oauth_token"):
        return {"connected": False, "staged": 0, "skipped": 0, "errors": []}
    service = build_gmail_service(account["oauth_token"])
    listing = service.users().messages().list(
        userId="me", q=SO_MAIL_QUERY, maxResults=max_messages
    ).execute()
    out = {"connected": True, "matched": 0, "staged": 0, "skipped": 0, "errors": [], "items": []}
    conn = sqlite3.connect(_db_path())
    try:
        ensure_schema(conn)
        known = {r[0] for r in conn.execute(
            "SELECT message_id FROM so_mail_imports WHERE user_id=? AND workspace_id=?",
            (user_id, workspace_id),
        ).fetchall()}
        for m in listing.get("messages") or []:
            out["matched"] += 1
            if m["id"] in known:
                out["skipped"] += 1
                continue
            try:
                mail = fetch_mail(service, m["id"])
                res = stage_mail(
                    conn, user_id=user_id, workspace_id=workspace_id, message_id=m["id"], mail=mail,
                    existing_payment=lambda did, season, cat: current_payment(db, user_id, did, season, cat),
                )
                final = res["status"]
                if res["new"] and final in ("review", "already_imported") and auto_save:
                    final = auto_process(conn, user_id=user_id, workspace_id=workspace_id,
                                         import_id=res["id"], db=db)
                out["staged"] += 1
                out["auto_saved"] = out.get("auto_saved", 0) + (1 if final == "auto_saved" else 0)
                out["items"].append({"id": res["id"], "status": final, "subject": mail.get("subject")})
            except Exception as exc:
                logger.exception("SO mail staging failed for %s", m["id"])
                out["errors"].append({"message_id": m["id"], "error": str(exc)})
        if auto_save:
            try:
                out["auto_saved"] = out.get("auto_saved", 0) + auto_process_open(
                    conn, user_id=user_id, workspace_id=workspace_id, db=db)
            except Exception as exc:
                logger.exception("SO mail auto-save of waiting mails failed")
                out["errors"].append({"error": f"auto-save: {exc}"})
    finally:
        conn.close()
    return out


# ------------------------------------------------------------- confirm


def set_due_date(conn: sqlite3.Connection, *, user_id: int, distributor_id: int, season: str,
                 category: str, due_date: str, mail_total: float | None, source: str) -> None:
    ensure_schema(conn)
    conn.execute(
        """INSERT INTO distributor_payment_due_dates
           (user_id, distributor_id, season, category, due_date, mail_total, source, updated_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(user_id, distributor_id, season, category)
           DO UPDATE SET due_date=excluded.due_date, mail_total=excluded.mail_total,
                         source=excluded.source, updated_at=excluded.updated_at""",
        (user_id, distributor_id, season, category, due_date, mail_total, source, _now()),
    )
    conn.commit()


def due_dates_for_user(conn: sqlite3.Connection, user_id: int) -> dict[tuple[int, str, str], dict]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT distributor_id, season, category, due_date, mail_total FROM distributor_payment_due_dates "
        "WHERE user_id = ?", (user_id,),
    ).fetchall()
    return {(int(d), s, c): {"due_date": dd, "mail_total": mt} for d, s, c, dd, mt in rows}


def run_order_desk_match(so_pack: dict[str, Any], filled_order_id: int) -> dict[str, Any]:
    """Call Order Desk's own FO↔SO match view (same code path as the UI button)."""
    from flask import current_app, request

    from app.routes.data import so_pack_match_filled_order

    user = getattr(request, "user", None)
    view = getattr(so_pack_match_filled_order, "__wrapped__", so_pack_match_filled_order)
    with current_app.test_request_context(
        "/api/v1/order-fulfillment/so-pack/match-filled-order",
        method="POST",
        json={"filled_order_id": filled_order_id, "so_pack": so_pack},
    ):
        from flask import request as inner

        inner.user = user
        resp = view()
    if isinstance(resp, tuple):
        resp = resp[0]
    try:
        body = resp.get_json()
    except Exception:
        body = None
    return {"status_code": getattr(resp, "status_code", 500), "body": body}


def confirm_import(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    workspace_id: str,
    import_id: int,
    overrides: dict[str, Any] | None = None,
    match_fn=None,
    db=None,
    auto: bool = False,
) -> dict[str, Any]:
    rec = get_import(conn, user_id, workspace_id, import_id, with_pack=True)
    if rec is None:
        raise LookupError("Import not found")
    if rec["status"] not in ("review", "already_imported"):
        raise ValueError(f"Import is {rec['status']}, cannot confirm")
    prop = dict(rec.get("proposal") or {})
    overrides = dict(overrides or {})
    answers = overrides.pop("answers", None) or {}
    prop.update({k: v for k, v in overrides.items() if v is not None})
    if any(v == "ignore" for v in answers.values()):
        reject_import(conn, user_id=user_id, workspace_id=workspace_id, import_id=import_id)
        return {"matches": [], "payment": {}, "ignored": True}
    if answers.get("filled_order") == "later":
        raise ValueError("Upload the Filled Order in Order Desk first, then check mails again")
    keep_payment = answers.get("payment") == "keep"
    dist = prop.get("distributor") or {}
    dist_id = prop.get("distributor_id") or dist.get("id")
    if not dist_id:
        raise ValueError("Pick the distributor first")
    season = prop.get("season")
    category = prop.get("category")
    if not season or not category:
        raise ValueError("Season and category are required")

    results: list[dict[str, Any]] = []
    if rec["status"] == "review":
        fo_ids = [int(x) for x in (prop.get("filled_order_ids") or [])]
        if not fo_ids:
            raise ValueError("Pick the Filled Order(s) to match")
        if not rec.get("pack"):
            raise ValueError("No analyzed SO pack stored for this mail")
        match_fn = match_fn or run_order_desk_match
        for fo_id in fo_ids:
            res = match_fn(rec["pack"], fo_id)
            body = res.get("body") or {}
            ok = res.get("status_code") == 200 and body.get("success") is not False
            data = body.get("data") if isinstance(body.get("data"), dict) else body
            results.append({"filled_order_id": fo_id, "ok": ok,
                            "run_id": (data or {}).get("run_id") if ok else None,
                            "message": (body.get("error") or {}).get("message")})
        if not any(r["ok"] for r in results):
            raise RuntimeError("Order Desk match failed: " + "; ".join(
                str(r.get("message") or "error") for r in results))

    if db is None:
        from app.routes.data import _db_path
        from centralized_db_system.db import CentralizedDB

        db = CentralizedDB(_db_path())
    undo = {
        "run_ids": [r["run_id"] for r in results if r.get("run_id")],
        "slot": [int(dist_id), season, category],
        "prev_payment": current_payment(db, user_id, int(dist_id), season, category),
        "prev_due": due_dates_for_user(conn, user_id).get((int(dist_id), season, category)),
    }
    payment: dict[str, Any] = {}
    if not keep_payment and prop.get("cd_percent") is not None:
        db.set_distributor_cd_rate(user_id, int(dist_id), season, float(prop["cd_percent"]), category)
        payment["cd_percent"] = float(prop["cd_percent"])
    if not keep_payment and prop.get("tds_applicable") is not None:
        db.set_distributor_tds_applicable(user_id, int(dist_id), season, category, bool(prop["tds_applicable"]))
        payment["tds_applicable"] = bool(prop["tds_applicable"])
    if prop.get("due_date"):
        set_due_date(conn, user_id=user_id, distributor_id=int(dist_id), season=season, category=category,
                     due_date=prop["due_date"], mail_total=prop.get("mail_total_payable"),
                     source=f"so_mail:{import_id}")
        payment["due_date"] = prop["due_date"]

    decisions = [q.get("auto_reason") for q in (prop.get("questions") or [])
                 if auto and q.get("auto_reason") and answers.get(q.get("key")) == q.get("auto")]
    result = {"matches": results, "payment": payment, "undo": undo, "previous_status": rec["status"],
              "auto_decisions": decisions}
    conn.execute(
        "UPDATE so_mail_imports SET status=?, result_json=?, proposal_json=?, updated_at=? "
        "WHERE id=? AND user_id=? AND workspace_id=?",
        ("auto_saved" if auto else "confirmed", json.dumps(result), json.dumps(prop), _now(),
         import_id, user_id, workspace_id),
    )
    conn.commit()
    return result


def current_payment(db, user_id: int, distributor_id: int, season: str, category: str,
                    *, status: list | None = None) -> dict | None:
    """CD % / TDS that Payment uses today for one slot (None when not set yet)."""
    if status is None:
        try:
            status = db.list_distributor_category_payment_status(user_id) or []
        except Exception:
            return None
    for d in status:
        if int(d.get("distributor_id") or 0) != int(distributor_id):
            continue
        for s in d.get("seasons") or []:
            if s.get("season") != season:
                continue
            for c in s.get("categories") or []:
                if c.get("category") == category:
                    return {"cd_percent": c.get("cd_percent"), "tds_applicable": bool(c.get("tds_applicable"))}
    return None


def undo_import(conn: sqlite3.Connection, *, user_id: int, workspace_id: str, import_id: int,
                db=None) -> dict[str, Any]:
    """Reverse a confirmed / auto-saved mail: remove the SO matches it created
    and put CD, TDS and due date back to what they were."""
    rec = get_import(conn, user_id, workspace_id, import_id)
    if rec is None:
        raise LookupError("Import not found")
    if rec["status"] not in ("confirmed", "auto_saved"):
        raise ValueError(f"Import is {rec['status']}, nothing to undo")
    undo = (rec.get("result") or {}).get("undo") or {}
    removed = 0
    for rid in undo.get("run_ids") or []:
        if matchdb.delete_match_run(conn, user_id, int(rid)):
            removed += 1
    slot = undo.get("slot") or []
    if len(slot) == 3:
        did, season, category = int(slot[0]), slot[1], slot[2]
        prev = undo.get("prev_payment")
        if db is None:
            from app.routes.data import _db_path
            from centralized_db_system.db import CentralizedDB

            db = CentralizedDB(_db_path())
        if prev:
            if prev.get("cd_percent") is not None:
                db.set_distributor_cd_rate(user_id, did, season, float(prev["cd_percent"]), category)
            db.set_distributor_tds_applicable(user_id, did, season, category, bool(prev.get("tds_applicable")))
        else:
            for table in ("distributor_cd_rates", "distributor_tds_prefs"):
                try:
                    conn.execute(f"DELETE FROM {table} WHERE user_id=? AND distributor_id=? AND season=? "
                                 "AND category=?", (user_id, did, season, category))
                except sqlite3.OperationalError:
                    pass
        prev_due = undo.get("prev_due")
        if prev_due:
            set_due_date(conn, user_id=user_id, distributor_id=did, season=season, category=category,
                         due_date=prev_due["due_date"], mail_total=prev_due.get("mail_total"),
                         source="so_mail:undo")
        else:
            ensure_schema(conn)
            conn.execute("DELETE FROM distributor_payment_due_dates WHERE user_id=? AND distributor_id=? "
                         "AND season=? AND category=?", (user_id, did, season, category))
    back = (rec.get("result") or {}).get("previous_status") or "review"
    conn.execute(
        "UPDATE so_mail_imports SET status=?, result_json=NULL, updated_at=? WHERE id=? AND user_id=? "
        "AND workspace_id=?", (back, _now(), import_id, user_id, workspace_id),
    )
    conn.commit()
    return {"removed_match_runs": removed, "status": back}


def auto_process(conn: sqlite3.Connection, *, user_id: int, workspace_id: str, import_id: int,
                 db=None, match_fn=None) -> str:
    """Save a staged mail on its own when every question has an automatic answer."""
    rec = get_import(conn, user_id, workspace_id, import_id)
    if not rec or rec["status"] not in ("review", "already_imported"):
        return rec["status"] if rec else "missing"
    prop = rec.get("proposal") or {}
    answers = auto_answers(prop.get("questions") or [])
    if not prop.get("auto_ok") or answers is None:
        return rec["status"]
    overrides: dict[str, Any] = {"answers": answers}
    if answers.get("distributor") == "pick" and (prop.get("distributor") or {}).get("id"):
        overrides["distributor_id"] = int(prop["distributor"]["id"])
    try:
        confirm_import(conn, user_id=user_id, workspace_id=workspace_id, import_id=import_id,
                       overrides=overrides, db=db, match_fn=match_fn, auto=True)
        return "auto_saved"
    except Exception as exc:
        # e.g. Order Desk asks replace/split — turn it into a question instead.
        prop = dict(prop)
        prop["auto_ok"] = False
        prop["questions"] = [q for q in (prop.get("questions") or []) if q.get("key") != "order_desk"] + [{
            "key": "order_desk",
            "text": f"Order Desk could not save automatically: {exc}",
            "options": [{"value": "match", "label": "Try again"},
                        {"value": "ignore", "label": "Ignore this mail"}],
        }]
        conn.execute("UPDATE so_mail_imports SET proposal_json=?, error=?, updated_at=? WHERE id=?",
                     (json.dumps(prop), str(exc)[:500], _now(), import_id))
        conn.commit()
        return rec["status"]


def auto_process_open(conn: sqlite3.Connection, *, user_id: int, workspace_id: str, db,
                      match_fn=None) -> int:
    """Re-check every waiting mail (Payment or Order Desk may have changed since)
    and save the ones NEXORA can now answer on its own. Returns how many saved."""
    items = [i for i in list_imports(conn, user_id, workspace_id)
             if i.get("status") in ("review", "already_imported")
             and not any(q.get("key") == "order_desk" for q in (i.get("proposal") or {}).get("questions") or [])]
    if not items:
        return 0
    try:
        status = db.list_distributor_category_payment_status(user_id) or []
    except Exception:
        status = None
    refresh_checks(items, existing_payment=lambda did, season, cat: current_payment(
        db, user_id, did, season, cat, status=status))
    saved = 0
    for item in items:
        conn.execute("UPDATE so_mail_imports SET proposal_json=? WHERE id=? AND user_id=? AND workspace_id=?",
                     (json.dumps(item["proposal"]), item["id"], user_id, workspace_id))
        conn.commit()
        if auto_process(conn, user_id=user_id, workspace_id=workspace_id, import_id=item["id"],
                        db=db, match_fn=match_fn) == "auto_saved":
            saved += 1
            status = None  # payment changed; reload for the next one
            try:
                status = db.list_distributor_category_payment_status(user_id) or []
            except Exception:
                pass
    return saved


def reject_import(conn: sqlite3.Connection, *, user_id: int, workspace_id: str, import_id: int) -> bool:
    ensure_schema(conn)
    # Auto-saved mails must be undone (not just hidden) before ignoring.
    cur = conn.execute(
        "UPDATE so_mail_imports SET status='rejected', updated_at=? "
        "WHERE id=? AND user_id=? AND workspace_id=? AND status IN ('review','already_imported','error')",
        (_now(), import_id, user_id, workspace_id),
    )
    conn.commit()
    return cur.rowcount > 0

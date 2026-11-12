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
) -> dict[str, Any]:
    """Parse + analyze + compare one mail and store it for review (idempotent)."""
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


def scan_mailbox(user_id: int, workspace_id: str, max_messages: int = 50) -> dict[str, Any]:
    """Stage new SO mails from the connected Gmail inbox."""
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
                res = stage_mail(conn, user_id=user_id, workspace_id=workspace_id,
                                 message_id=m["id"], mail=mail)
                out["staged"] += 1
                out["items"].append({"id": res["id"], "status": res["status"], "subject": mail.get("subject")})
            except Exception as exc:
                logger.exception("SO mail staging failed for %s", m["id"])
                out["errors"].append({"message_id": m["id"], "error": str(exc)})
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
) -> dict[str, Any]:
    rec = get_import(conn, user_id, workspace_id, import_id, with_pack=True)
    if rec is None:
        raise LookupError("Import not found")
    if rec["status"] not in ("review", "already_imported"):
        raise ValueError(f"Import is {rec['status']}, cannot confirm")
    prop = dict(rec.get("proposal") or {})
    prop.update({k: v for k, v in (overrides or {}).items() if v is not None})
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
            ok = res.get("status_code") == 200 and (res.get("body") or {}).get("success") is not False
            results.append({"filled_order_id": fo_id, "ok": ok,
                            "message": ((res.get("body") or {}).get("error") or {}).get("message")})
        if not any(r["ok"] for r in results):
            raise RuntimeError("Order Desk match failed: " + "; ".join(
                str(r.get("message") or "error") for r in results))

    if db is None:
        from app.routes.data import _db_path
        from centralized_db_system.db import CentralizedDB

        db = CentralizedDB(_db_path())
    payment: dict[str, Any] = {}
    if prop.get("cd_percent") is not None:
        db.set_distributor_cd_rate(user_id, int(dist_id), season, float(prop["cd_percent"]), category)
        payment["cd_percent"] = float(prop["cd_percent"])
    if prop.get("tds_applicable") is not None:
        db.set_distributor_tds_applicable(user_id, int(dist_id), season, category, bool(prop["tds_applicable"]))
        payment["tds_applicable"] = bool(prop["tds_applicable"])
    if prop.get("due_date"):
        set_due_date(conn, user_id=user_id, distributor_id=int(dist_id), season=season, category=category,
                     due_date=prop["due_date"], mail_total=prop.get("mail_total_payable"),
                     source=f"so_mail:{import_id}")
        payment["due_date"] = prop["due_date"]

    result = {"matches": results, "payment": payment}
    conn.execute(
        "UPDATE so_mail_imports SET status='confirmed', result_json=?, proposal_json=?, updated_at=? "
        "WHERE id=? AND user_id=? AND workspace_id=?",
        (json.dumps(result), json.dumps(prop), _now(), import_id, user_id, workspace_id),
    )
    conn.commit()
    return result


def reject_import(conn: sqlite3.Connection, *, user_id: int, workspace_id: str, import_id: int) -> bool:
    ensure_schema(conn)
    cur = conn.execute(
        "UPDATE so_mail_imports SET status='rejected', updated_at=? "
        "WHERE id=? AND user_id=? AND workspace_id=? AND status IN ('review','already_imported','error')",
        (_now(), import_id, user_id, workspace_id),
    )
    conn.commit()
    return cur.rowcount > 0

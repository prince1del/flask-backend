"""NEXORA Guard — AI that watches the books and catches mistakes early.

Rules run over the data and raise *issues*; each issue lives in an inbox:
open → resolved (automatically, once the data is fixed) or dismissed (by the user).
On top of the rules, AI explains any issue in plain words with steps to fix it,
and writes a short daily digest. Nothing here changes business data.

Checks today
* House of Prizm (HoP workspace, for hop_admin):
  overdue customer bills, overdue vendor bills, negative cash/bank, negative
  stock, duplicate bill / invoice numbers, B2B invoices without HSN, old open
  cheques, unconverted estimates, unused advances while bills are unpaid,
  bill balances that do not add up, books not balancing.
* Bombay Dyeing distribution (every login with distributor payments):
  overdue distributor payments, money received with no CI raised, billed more
  than received — from the Distributor Hisaab.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

SEVERITY_ORDER = {"alert": 0, "warn": 1, "info": 2}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _inr(v: Any) -> str:
    """₹12,34,567 (Indian grouping)."""
    n = int(round(_f(v)))
    digits = str(abs(n))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups) + "," + tail
    return ("-₹" if n < 0 else "₹") + digits


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS guard_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            user_id INTEGER NOT NULL DEFAULT 0,      -- 0 = whole company (HoP); else one login (BD)
            key TEXT NOT NULL,                       -- stable id of the problem
            area TEXT NOT NULL,
            severity TEXT NOT NULL,                  -- alert | warn | info
            title TEXT NOT NULL,
            detail TEXT,
            link TEXT,                               -- screen to open, e.g. "hop:invoices"
            amount REAL,
            status TEXT NOT NULL DEFAULT 'open',     -- open | resolved | dismissed
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            resolved_at TEXT,
            UNIQUE(workspace_id, user_id, key)
        );
        CREATE INDEX IF NOT EXISTS idx_guard_issues_scope ON guard_issues(workspace_id, user_id, status);
        CREATE TABLE IF NOT EXISTS guard_runs (
            workspace_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            ran_at TEXT NOT NULL,
            PRIMARY KEY (workspace_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS guard_digests (
            workspace_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (workspace_id, user_id, day)
        );
        """
    )


def _issue(key, area, severity, title, detail="", link="", amount=None) -> dict[str, Any]:
    return {"key": key, "area": area, "severity": severity, "title": title, "detail": detail,
            "link": link, "amount": round(_f(amount), 2) if amount is not None else None}


# ── HoP checks ───────────────────────────────────────────────────────────────

def hop_findings(conn: sqlite3.Connection, ws: str, today: date | None = None) -> list[dict[str, Any]]:
    from app import hop_money, hop_reports
    from app.hop_schema import ensure_hop_schema  # noqa: F401

    today = today or date.today()
    conn.row_factory = sqlite3.Row
    live = "(deleted_at IS NULL OR trim(deleted_at)='')"
    out: list[dict[str, Any]] = []

    def rows(sql, params=()):
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            return []

    # 1–2) Overdue bills, grouped by party.
    for ty, who, sev_area in ((1, "customer", "Money to collect"), (2, "vendor", "Bills to pay")):
        due_rows = rows(
            f"""SELECT party_name, party_id, txn_number, txn_date, due_date, balance_amount FROM hop_party_transactions
                WHERE workspace_id=? AND {live} AND txn_type=? AND balance_amount > 0.5""", (ws, ty))
        by_party: dict[str, list[dict]] = {}
        for r in due_rows:
            due = (r.get("due_date") or "")[:10] or (
                (date.fromisoformat(r["txn_date"][:10]) + timedelta(days=30)).isoformat() if r.get("txn_date") else "")
            if due and due < today.isoformat():
                r["_due"] = due
                by_party.setdefault(r["party_name"] or "—", []).append(r)
        for party, bills in by_party.items():
            total = sum(_f(b["balance_amount"]) for b in bills)
            oldest = min(b["_due"] for b in bills)
            days = (today - date.fromisoformat(oldest)).days
            if ty == 1:
                title = f"{party} owes {_inr(total)} — overdue {days} days"
            else:
                title = f"Pay {party} {_inr(total)} — overdue {days} days"
            out.append(_issue(f"hop:overdue:{ty}:{party.lower()}", sev_area, "alert" if days > 45 else "warn", title,
                              f"{len(bills)} bill(s): " + ", ".join(f"{b['txn_number']} ({_inr(b['balance_amount'])})" for b in bills[:6]),
                              "hop:receivables" if ty == 1 else "hop:purchase_bills", total))

    # 3) Cash / bank below zero.
    for a in rows("SELECT name, current_balance FROM hop_bank_accounts WHERE workspace_id=? AND COALESCE(is_active,1)=1", (ws,)):
        if _f(a["current_balance"]) < -0.5:
            out.append(_issue(f"hop:negcash:{a['name'].lower()}", "Cash & Bank", "alert",
                              f"{a['name']} is below zero ({_inr(a['current_balance'])})",
                              "More money went out than came in. A receipt may be missing, or a payment was put in the "
                              "wrong account.", "hop:banking", a["current_balance"]))

    # 4) Stock below zero.
    neg = rows(f"SELECT name, stock_qty FROM hop_products WHERE workspace_id=? AND stock_qty < 0 AND {live} "
               "ORDER BY stock_qty LIMIT 20", (ws,))
    if neg:
        out.append(_issue("hop:negstock", "Stock", "warn", f"{len(neg)} item(s) have stock below zero",
                          ", ".join(f"{r['name']} ({r['stock_qty']:g})" for r in neg[:8]) +
                          ". A purchase bill may be missing, or the item name on a bill doesn't match the catalogue.",
                          "hop:stock"))

    # 5) Same bill / invoice number used twice.
    for ty, label in ((1, "Sale invoice"), (2, "Purchase bill")):
        for r in rows(f"""SELECT lower(trim(txn_number)) AS n, party_name, COUNT(*) AS c FROM hop_party_transactions
                          WHERE workspace_id=? AND {live} AND txn_type=? AND trim(coalesce(txn_number,''))!=''
                          GROUP BY n{', party_id' if ty == 2 else ''} HAVING c > 1""", (ws, ty)):
            out.append(_issue(f"hop:dup:{ty}:{r['n']}:{(r['party_name'] or '').lower() if ty == 2 else ''}", "Mistakes", "alert",
                              f"{label} number {r['n'].upper()} is used {r['c']} times" + (f" for {r['party_name']}" if ty == 2 else ""),
                              "The same bill may have been entered twice — that doubles the amount and stock.",
                              "hop:invoices" if ty == 1 else "hop:purchase_bills"))

    # 6) B2B invoices (customer has GSTIN) with lines missing HSN.
    miss = rows(f"""SELECT DISTINCT t.txn_number FROM hop_party_transactions t
                    JOIN hop_customers c ON c.id=t.party_id AND c.workspace_id=t.workspace_id
                    JOIN hop_txn_lines l ON l.workspace_id=t.workspace_id AND l.source_txn_id=t.source_txn_id
                    WHERE t.workspace_id=? AND (t.deleted_at IS NULL OR trim(t.deleted_at)='') AND t.txn_type=1
                      AND t.party_type='customer' AND trim(coalesce(c.gst_no,''))!='' AND trim(coalesce(l.hsn,''))='' AND COALESCE(l.is_charge,0)=0
                      AND date(t.txn_date) >= date(?)""", (ws, (today - timedelta(days=120)).isoformat()))
    if miss:
        out.append(_issue("hop:gst:hsn", "GST", "warn", f"{len(miss)} B2B invoice(s) have items without HSN",
                          "GSTR-1 needs HSN for B2B sales: " + ", ".join(m["txn_number"] or "—" for m in miss[:8]), "hop:invoices"))

    # 7) Cheques still open a week after their date.
    old = (today - timedelta(days=7)).isoformat()
    for c in rows("""SELECT c.*, t.party_name FROM hop_cheques c JOIN hop_party_transactions t ON t.id=c.party_txn_id
                     WHERE c.workspace_id=? AND c.status='open' AND date(coalesce(c.cheque_date, c.created_at)) < date(?)""",
                  (ws, old)):
        word = "deposit" if c["direction"] == "in" else "check it was withdrawn for"
        out.append(_issue(f"hop:cheque:{c['id']}", "Cash & Bank", "warn",
                          f"Cheque {c.get('cheque_no') or ''} from {c['party_name']} ({_inr(c['amount'])}) is still open",
                          f"Cheque date {c.get('cheque_date') or '—'}. Please {word} it in Cash & Bank → Cheques.",
                          "hop:banking", c["amount"]))

    # 8) Estimates not converted for 30+ days.
    est = rows(f"""SELECT COUNT(*) AS c, COALESCE(SUM(total_amount),0) AS v FROM hop_party_transactions
                   WHERE workspace_id=? AND {live} AND txn_type=27 AND converted_to_txn_id IS NULL
                   AND source_txn_id < 0 AND date(txn_date) < date(?)""", (ws, (today - timedelta(days=30)).isoformat()))
    if est and est[0]["c"]:
        out.append(_issue("hop:estimates", "Sales follow-up", "info",
                          f"{est[0]['c']} estimate(s) worth {_inr(est[0]['v'])} not converted for 30+ days",
                          "Follow up with these customers, or mark the estimates as lost.", "hop:sale_estimates", est[0]["v"]))

    # 9) Advance sitting unused while the same party has unpaid bills.
    for r in rows(f"""SELECT p.party_name, p.party_id, SUM(p.unused_amount) AS adv FROM hop_party_transactions p
                      WHERE p.workspace_id=? AND (p.deleted_at IS NULL OR trim(p.deleted_at)='') AND p.txn_type=3
                        AND p.unused_amount > 0.5 GROUP BY p.party_id""", (ws,)):
        if hop_money.open_bills(conn, ws, 3, "customer", int(r["party_id"] or 0)):
            out.append(_issue(f"hop:advance:{r['party_id']}", "Mistakes", "warn",
                              f"{r['party_name']}: advance {_inr(r['adv'])} not set against their unpaid bills",
                              "Open the Payment-In and tick the bills it pays, so the dues are right.", "hop:payments", r["adv"]))

    # 10) Bill balances that do not add up (data integrity).
    for t in rows(f"""SELECT id, txn_number, total_amount, received_amount, balance_amount FROM hop_party_transactions
                      WHERE workspace_id=? AND {live} AND txn_type IN (1,2) AND received_amount IS NOT NULL""", (ws,)):
        linked = _f(conn.execute("SELECT COALESCE(SUM(amount),0) FROM hop_payment_links WHERE workspace_id=? AND bill_txn_id=?",
                                 (ws, t["id"])).fetchone()[0])
        expect = max(0.0, _f(t["total_amount"]) - _f(t["received_amount"]) - linked)
        if abs(expect - _f(t["balance_amount"])) > 1:
            out.append(_issue(f"hop:balance:{t['id']}", "Mistakes", "alert",
                              f"Balance of {t['txn_number']} doesn't add up",
                              f"Shows {_inr(t['balance_amount'])}, should be {_inr(expect)}. Open and save the bill once to fix it.",
                              "hop:invoices"))

    # 11) Books must balance.
    try:
        if not hop_reports.trial_balance(conn, ws)["balanced"] or not hop_reports.balance_sheet(conn, ws)["balanced"]:
            out.append(_issue("hop:books", "Accounts", "alert", "Books are not balancing",
                              "Trial Balance or Balance Sheet does not balance. Please tell support.", "hop:reports"))
    except Exception:
        pass
    return out


# ── Bombay Dyeing distribution checks ────────────────────────────────────────

def bd_findings(conn: sqlite3.Connection, user_id: int, ws: str) -> list[dict[str, Any]]:
    from app.services.distributor_ledger import build_ledger
    from centralized_db_system.db import CentralizedDB
    from flask import current_app

    try:
        ledger = build_ledger(CentralizedDB(current_app.config["DATABASE_PATH"]), conn, user_id=user_id, workspace_id=ws)
    except Exception:
        return []
    out = []
    for d in ledger.get("distributors") or []:
        name = d.get("distributor_name") or "Distributor"
        for a in d.get("alerts") or []:
            if a.get("level") not in ("alert", "warn"):
                continue
            text = str(a.get("text") or "")
            out.append(_issue(f"bd:{d.get('distributor_id')}:{text.split(':')[0].lower()}:{a['level']}", "Distributor payments",
                              a["level"], f"{name} — {text}", "From Distributor Hisaab.", "bd:hisaab"))
    return out


# ── Inbox ────────────────────────────────────────────────────────────────────

def can_open_hop(user: dict[str, Any]) -> bool:
    """Same rule as the HoP screens (require_role hop_admin, or the company owner)."""
    from app.hop_schema import HOP_WORKSPACE_ID

    return (user.get("role") == "hop_admin" or bool(user.get("is_workspace_owner"))
            or str(user.get("workspace_id") or "") == HOP_WORKSPACE_ID)


def _scope(user: dict[str, Any], db_path: str = "") -> tuple[str, int, bool]:
    """(workspace, inbox id, is_hop). HoP and BD are two separate businesses with two separate inboxes:
    the screen says which one (user["guard_scope"] = "hop" | "bd"); HoP uses the same workspace as the
    HoP screens (the login's own workspace), inbox id 0; BD uses the login's own inbox (its user id)."""
    from app.hop_schema import HOP_WORKSPACE_ID

    ws = str(user.get("workspace_id") or "default")
    business = str(user.get("guard_scope") or "").lower()
    try:   # the calling UI decides the inbox: Executive → BD only, Business / Distributor → their books only
        from flask import has_request_context, request

        if has_request_context():
            ui = (request.headers.get("X-Nexora-UI") or "").strip().lower()
            if ui == "executive":
                business = "bd"
            elif ui in ("business", "distributor"):
                business = "hop"
    except Exception:
        pass
    if business not in ("hop", "bd"):
        business = "hop" if (user.get("role") == "hop_admin" or ws == HOP_WORKSPACE_ID) else "bd"
    if business == "hop":
        if not can_open_hop(user):
            raise PermissionError("The business books are not open for this login")
        return _firm_ws(db_path, ws), 0, True
    return ws, int(user.get("user_id") or 0), False


def _firm_ws(db_path: str, base_ws: str) -> str:
    """The firm of the calling UI (X-Nexora-UI) + chosen firm (X-Hop-Firm): the Distributor UI's Guard checks
    only the Distributor books, the Business UI's only the Business books — never each other's."""
    try:
        from flask import has_request_context, request

        if not db_path or not has_request_context():
            return base_ws
        from app import hop_firms

        with sqlite3.connect(db_path) as conn:
            return hop_firms.active_ws(conn, base_ws, request.headers.get("X-Hop-Firm"))
    except Exception:
        return base_ws


def run(db_path: str, user: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    ws, uid, is_hop = _scope(user, db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        if is_hop:
            from app.hop_schema import ensure_hop_schema

            ensure_hop_schema(db_path)
            found = hop_findings(conn, ws, today)
        else:
            found = bd_findings(conn, int(user.get("user_id") or 0), ws)
        now = _now()
        keys = set()
        new = 0
        for f in found:
            keys.add(f["key"])
            cur = conn.execute("SELECT id, status FROM guard_issues WHERE workspace_id=? AND user_id=? AND key=?",
                               (ws, uid, f["key"])).fetchone()
            if cur is None:
                conn.execute("""INSERT INTO guard_issues (workspace_id, user_id, key, area, severity, title, detail, link,
                                amount, status, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?, 'open', ?, ?)""",
                             (ws, uid, f["key"], f["area"], f["severity"], f["title"], f["detail"], f["link"],
                              f["amount"], now, now))
                new += 1
            else:
                status = cur["status"]
                if status == "resolved":
                    status = "open"  # came back
                conn.execute("""UPDATE guard_issues SET area=?, severity=?, title=?, detail=?, link=?, amount=?, status=?,
                                last_seen=?, resolved_at=CASE WHEN ?='open' THEN NULL ELSE resolved_at END WHERE id=?""",
                             (f["area"], f["severity"], f["title"], f["detail"], f["link"], f["amount"], status,
                              now, status, cur["id"]))
        # Problems no longer found are fixed.
        for r in conn.execute("SELECT id, key FROM guard_issues WHERE workspace_id=? AND user_id=? AND status='open'",
                              (ws, uid)).fetchall():
            if r["key"] not in keys:
                conn.execute("UPDATE guard_issues SET status='resolved', resolved_at=? WHERE id=?", (now, r["id"]))
        conn.execute("INSERT OR REPLACE INTO guard_runs (workspace_id, user_id, ran_at) VALUES (?, ?, ?)", (ws, uid, now))
        conn.commit()
    return {"found": len(found), "new": new}


def issues(db_path: str, user: dict[str, Any], status: str = "open", *, refresh_minutes: int = 10) -> dict[str, Any]:
    ws, uid, _ = _scope(user, db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        last = conn.execute("SELECT ran_at FROM guard_runs WHERE workspace_id=? AND user_id=?", (ws, uid)).fetchone()
    stale = last is None or (datetime.now(timezone.utc) - datetime.fromisoformat(last[0])).total_seconds() > refresh_minutes * 60
    if stale:
        run(db_path, user)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM guard_issues WHERE workspace_id=? AND user_id=?"
        params: list[Any] = [ws, uid]
        if status in ("open", "resolved", "dismissed"):
            sql += " AND status=?"
            params.append(status)
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY last_seen DESC LIMIT 300", params).fetchall()]
        counts = {r[0]: r[1] for r in conn.execute(
            "SELECT status, COUNT(*) FROM guard_issues WHERE workspace_id=? AND user_id=? GROUP BY status", (ws, uid))}
    rows.sort(key=lambda r: (SEVERITY_ORDER.get(r["severity"], 3), -abs(_f(r.get("amount")))))
    return {"issues": rows, "counts": counts,
            "alerts": sum(1 for r in rows if r["status"] == "open" and r["severity"] == "alert")}


def set_status(db_path: str, user: dict[str, Any], issue_id: int, status: str) -> dict[str, Any]:
    if status not in ("open", "dismissed"):
        raise ValueError("status must be open or dismissed")
    ws, uid, _ = _scope(user, db_path)
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        cur = conn.execute("UPDATE guard_issues SET status=? WHERE id=? AND workspace_id=? AND user_id=?",
                           (status, int(issue_id), ws, uid))
        if cur.rowcount == 0:
            raise LookupError("Issue not found")
        conn.commit()
    return {"id": int(issue_id), "status": status}


# ── AI ───────────────────────────────────────────────────────────────────────

def _gemini(prompt: str, *, user_id: int, feature: str, max_tokens: int) -> str:
    from app.services import nexora_ai_agent as agent
    from app.services.dsr_notes_polish import gemini_api_key

    if agent.over_daily_limit(user_id, "ai"):
        raise ValueError("Daily AI limit reached.")
    key = gemini_api_key()
    if not key:
        raise ValueError("AI is not set up on the server.")
    body = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens}}).encode("utf-8")
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
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    raise ValueError("AI is not available right now.")


def explain(db_path: str, user: dict[str, Any], issue_id: int) -> str:
    ws, uid, is_hop = _scope(user, db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        r = conn.execute("SELECT * FROM guard_issues WHERE id=? AND workspace_id=? AND user_id=?",
                         (int(issue_id), ws, uid)).fetchone()
    if not r:
        raise LookupError("Issue not found")
    biz = "a small business (billing and accounts)" if is_hop else "a Bombay Dyeing distribution sales executive"
    prompt = (f"You help {biz} keep its books right. Explain this warning in simple English (short sentences, "
              "no jargon), why it matters in money terms, and give 2-4 numbered steps to fix it in the NEXORA app. "
              f"Under 120 words.\n\nWarning: {r['title']}\nDetails: {r['detail'] or '-'}\nArea: {r['area']}")
    return _gemini(prompt, user_id=int(user.get("user_id") or 0), feature="nexora_agent_guard_explain", max_tokens=350)


def digest(db_path: str, user: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """Today's short AI summary of the open issues (one AI call per day per inbox)."""
    ws, uid, _ = _scope(user, db_path)
    day = date.today().isoformat()
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        if not force:
            got = conn.execute("SELECT text FROM guard_digests WHERE workspace_id=? AND user_id=? AND day=?",
                               (ws, uid, day)).fetchone()
            if got:
                return {"day": day, "text": got[0], "cached": True}
    data = issues(db_path, user, "open")
    open_rows = data["issues"]
    if not open_rows:
        text = "All clear today — NEXORA Guard found nothing that needs your attention."
    else:
        lines = "\n".join(f"- [{r['severity']}] {r['title']}" for r in open_rows[:25])
        prompt = ("Write today's 'NEXORA Guard' note for a small business owner: 3-5 short bullet points in simple "
                  "English, most money-important first, each ending with what to do. Use only these findings:\n" + lines)
        try:
            text = _gemini(prompt, user_id=int(user.get("user_id") or 0), feature="nexora_agent_guard_digest", max_tokens=400)
        except ValueError:
            text = "\n".join(f"• {r['title']}" for r in open_rows[:5])
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT OR REPLACE INTO guard_digests (workspace_id, user_id, day, text, created_at) VALUES (?,?,?,?,?)",
                     (ws, uid, day, text, _now()))
        conn.commit()
    return {"day": day, "text": text, "cached": False}

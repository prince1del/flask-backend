"""Payment reminders and the daily backup email (business UI).

- due_list(): sale invoices with money due — overdue, due today, due soon — each with a ready reminder
  text and WhatsApp link (owner's template from Settings → Sharing).
- run_daily(): once a day (systemd timer, 9 am IST): emails customers whose bill is due / overdue when
  the owner switched that on, sends the owner one summary email, and (if on) emails the backup zip.
  Nothing is sent unless the owner turned it on in Settings → Reminders; every send is logged so a
  customer is not reminded again before the repeat gap.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

log = logging.getLogger(__name__)
SALE_TYPES = (1,)  # sale invoices


def _today() -> date:
    # India time for "today" (UTC+5:30)
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_reminder_log (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               workspace_id TEXT NOT NULL,
               party_txn_id INTEGER,
               channel TEXT NOT NULL,          -- email / whatsapp / summary / backup
               sent_to TEXT,
               sent_at TEXT NOT NULL
           )""")


def _settings(conn, ws) -> dict[str, Any]:
    from app import hop_app_settings

    return hop_app_settings.get_all(conn, ws)


def _last_sent(conn, ws, txn_id: int) -> str | None:
    r = conn.execute("SELECT MAX(sent_at) FROM hop_reminder_log WHERE workspace_id=? AND party_txn_id=? "
                     "AND channel IN ('email','whatsapp')", (ws, int(txn_id))).fetchone()
    return r[0] if r else None


def due_list(conn: sqlite3.Connection, ws: str, today: date | None = None) -> dict[str, Any]:
    """Bills with money to collect, grouped, each with reminder text + WhatsApp link + customer email."""
    from app import hop_share

    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    today = today or _today()
    s = _settings(conn, ws)
    soon_days = int(s.get("reminder_days_before") or 2)
    rows = conn.execute(
        f"""SELECT t.id, t.txn_number, t.party_id, t.party_name, t.txn_date, t.due_date, t.balance_amount, t.total_amount
            FROM hop_party_transactions t
            WHERE t.workspace_id=? AND t.txn_type IN ({','.join('?' * len(SALE_TYPES))})
              AND (t.deleted_at IS NULL OR trim(t.deleted_at)='') AND COALESCE(t.balance_amount,0) > 0.009
            ORDER BY COALESCE(t.due_date, t.txn_date)""", (ws, *SALE_TYPES)).fetchall()
    out: dict[str, list] = {"overdue": [], "today": [], "soon": []}
    for r in rows:
        due_s = (r["due_date"] or "")[:10]
        try:
            due = date.fromisoformat(due_s) if due_s else None
        except ValueError:
            due = None
        if due is None:
            continue
        days = (due - today).days
        group = "overdue" if days < 0 else "today" if days == 0 else "soon" if days <= soon_days else None
        if not group:
            continue
        email = ""
        if r["party_id"]:
            c = conn.execute("SELECT email FROM hop_customers WHERE workspace_id=? AND id=?", (ws, int(r["party_id"]))).fetchone()
            email = (c[0] or "").strip() if c else ""
        try:
            share = hop_share.share_payload(conn, ws, int(r["id"]), "reminder")
        except Exception:
            share = {"text": "", "whatsapp_url": "", "phone": ""}
        out[group].append({
            "id": r["id"], "number": r["txn_number"], "party": r["party_name"], "due_date": due_s,
            "days": days, "balance": round(float(r["balance_amount"] or 0), 2),
            "email": email, "phone": share.get("phone") or "", "text": share.get("text") or "",
            "whatsapp_url": share.get("whatsapp_url") or "", "last_reminded": _last_sent(conn, ws, int(r["id"])),
        })
    total = {k: round(sum(x["balance"] for x in v), 2) for k, v in out.items()}
    return {**out, "totals": total, "as_of": today.isoformat(),
            "email_customers_on": bool(s.get("reminder_email_customers")), "soon_days": soon_days}


def mark_sent(conn: sqlite3.Connection, ws: str, txn_id: int, channel: str, to: str = "") -> None:
    ensure_schema(conn)
    conn.execute("INSERT INTO hop_reminder_log (workspace_id, party_txn_id, channel, sent_to, sent_at) VALUES (?,?,?,?,?)",
                 (ws, int(txn_id) if txn_id else None, channel, to[:120], datetime.now(timezone.utc).isoformat()))
    conn.commit()


def email_reminder(conn: sqlite3.Connection, ws: str, item: dict[str, Any], firm_name: str, reply_to: str = "") -> tuple[bool, str]:
    from app.services.outbound_mail import send_plain_email

    if not item.get("email"):
        return False, "This customer has no email"
    subject = f"Payment reminder — {item.get('number') or 'invoice'} — {firm_name or 'NEXORA'}"
    body = (item.get("text") or "").strip() + f"\n\n— {firm_name}\n(Sent by NEXORA on behalf of {firm_name}"
    body += f"; reply to {reply_to})" if reply_to else ")"
    ok, err = send_plain_email(to=item["email"], subject=subject, body=body)
    if ok:
        mark_sent(conn, ws, int(item["id"]), "email", item["email"])
    return ok, err


def _owner_email(conn, ws: str) -> str:
    try:
        r = conn.execute("SELECT email FROM hop_firm_profile WHERE workspace_id=?", (ws,)).fetchone()
        if r and (r[0] or "").strip():
            return r[0].strip()
    except sqlite3.Error:
        pass
    base = ws.split("__f")[0]
    try:
        r = conn.execute("SELECT email FROM users WHERE workspace_id=? AND email IS NOT NULL AND trim(email)<>'' "
                         "ORDER BY IFNULL(is_workspace_owner,0) DESC, CASE WHEN role='hop_admin' THEN 0 ELSE 1 END, id LIMIT 1",
                         (base,)).fetchone()
        return (r[0] or "").strip() if r else ""
    except sqlite3.Error:
        return ""


def _money(v: float) -> str:
    return f"₹{v:,.2f}"


def run_for_workspace(conn: sqlite3.Connection, ws: str, today: date | None = None,
                      only: set[str] | None = None) -> dict[str, Any]:
    """Everything the owner switched on, for one business. Returns what was sent (for logs / tests)."""
    from app import hop_backup, hop_firms
    from app.services.outbound_mail import send_email, smtp_configured

    sent = {"customers": 0, "summary": False, "backup": False, "skipped": []}
    if not smtp_configured():
        sent["skipped"].append("email not set up")
        return sent
    s = _settings(conn, ws)
    firm = hop_firms.business_name(conn, ws, "Your business")
    owner = _owner_email(conn, ws)
    dl = due_list(conn, ws, today)
    repeat = max(1, int(s.get("reminder_repeat_days") or 7))
    now = datetime.now(timezone.utc)
    want = lambda k: only is None or k in only  # noqa: E731
    if want("customers") and s.get("reminder_email_customers"):
        for grp in ("overdue", "today", "soon"):
            for it in dl[grp]:
                if not it["email"]:
                    continue
                last = it.get("last_reminded")
                if last and (now - datetime.fromisoformat(last)) < timedelta(days=repeat):
                    continue
                ok, _ = email_reminder(conn, ws, it, firm, owner)
                sent["customers"] += 1 if ok else 0
    if want("summary") and (s.get("reminder_owner_summary") or only) and owner and (dl["overdue"] or dl["today"] or dl["soon"]):
        lines = [f"Good morning! Money to collect for {firm} — {dl['as_of']}", ""]
        for title, grp in (("Overdue", "overdue"), ("Due today", "today"), (f"Due in {dl['soon_days']} days", "soon")):
            if dl[grp]:
                lines.append(f"{title}: {len(dl[grp])} bills, {_money(dl['totals'][grp])}")
                for it in dl[grp][:15]:
                    extra = f", {-it['days']} days late" if it["days"] < 0 else ""
                    lines.append(f"  • {it['party']} — {it['number']} — {_money(it['balance'])}{extra}")
                lines.append("")
        lines.append("Open NEXORA → Reminders to send WhatsApp reminders in one tap.")
        ok, _ = send_email(to=owner, subject=f"NEXORA — money to collect today ({firm})", body="\n".join(lines))
        if ok:
            sent["summary"] = True
            mark_sent(conn, ws, 0, "summary", owner)
    if want("backup") and (s.get("backup_email_daily") or only) and owner:
        data, counts = hop_backup.build_zip(conn, ws)
        if len(data) > 20 * 1024 * 1024:
            send_email(to=owner, subject=f"NEXORA backup — {firm}",
                       body="Your backup is too big to email today. Download it from Settings → Backup.")
        else:
            stamp = (now + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
            ok, _ = send_email(to=owner, subject=f"NEXORA daily backup — {firm} — {stamp}",
                               body=(f"Your NEXORA backup for {firm} is attached ({sum(counts.values())} rows).\n"
                                     "Keep it safe; it has your business data. Turn this off in Settings → Reminders."),
                               attachments=[(f"nexora_backup_{stamp}.zip", data, "application/zip")])
            if ok:
                sent["backup"] = True
                mark_sent(conn, ws, 0, "backup", owner)
    return sent


def run_daily(db_path: str) -> list[dict[str, Any]]:
    """All businesses (every firm) — called by the daily timer."""
    results = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        try:
            wss = [r[0] for r in conn.execute("SELECT DISTINCT workspace_id FROM hop_party_transactions").fetchall()]
        except sqlite3.Error:
            wss = []
        for ws in wss:
            try:
                res = run_for_workspace(conn, ws)
                results.append({"workspace": ws, **res})
            except Exception as exc:  # one business failing never stops the others
                log.exception("reminders failed for %s", ws)
                results.append({"workspace": ws, "error": str(exc)})
    return results


if __name__ == "__main__":
    import os

    from app.hop_schema import ensure_hop_schema

    path = os.path.join((os.getenv("PERSISTENT_DISK_PATH") or "/var/data").strip(), "centralized_db.sqlite3")
    path = os.getenv("DATABASE_PATH") or path
    ensure_hop_schema(path)
    for r in run_daily(path):
        print(r)

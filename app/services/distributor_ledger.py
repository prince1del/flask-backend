"""Distributor Hisaab (ledger): Order vs Payment vs Billing per distributor.

For every distributor → season → category (Bed / Bath / TOB …):

* Order payable  = SO bill (incl. GST) − Cash Discount − TDS   (Payment Status)
* Received       = deposits recorded against that category
* Billed         = Commercial Invoices raised on that category's SOs, valued
                   the same way as the order: CI gross − CD% × CI taxable
                   − TDS% × CI gross (when TDS applies)

and two answers:

* Unbilled advance = Received − Billed  → money received, CI still to be raised
                                          (negative = billed more than received)
* Payment due      = Order payable − Received → still to collect
                                          (negative = distributor paid extra)

Deposits are applied to CIs oldest-first so every CI shows how much of it is
covered and which money is still sitting as advance.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from app.services import fo_so_match_db as matchdb

_TOL = 1.0  # rupees


def _so_slots(conn: sqlite3.Connection, user_id: int) -> dict[str, tuple[int, str, str]]:
    """SO number → (distributor_id, season, category) from the latest match runs."""
    out: dict[str, tuple[int, str, str]] = {}
    try:
        rows = conn.execute(
            """SELECT distributor_id, season, category, rows_json FROM fo_so_match_runs
               WHERE user_id = ? AND distributor_id IS NOT NULL ORDER BY id""",
            (user_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for dist_id, season, category, rows_json in rows:
        try:
            parsed = json.loads(rows_json) if rows_json else []
        except (TypeError, ValueError):
            continue
        for so in matchdb._compute_so_totals_from_rows(parsed if isinstance(parsed, list) else []):
            key = matchdb.normalize_so_number(so) or str(so).strip()
            if key:
                out[key] = (int(dist_id), season or "", category or "")
    return out


def _ci_rows(conn: sqlite3.Connection, workspace_id: str) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """SELECT tracking_id, distributor_id, order_ref_no, commercial_invoice_parsed
               FROM order_lifecycle_tracking
               WHERE commercial_invoice_parsed IS NOT NULL AND workspace_id = ?""",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for tid, dist_id, ref, raw in rows:
        try:
            parsed = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            continue
        header = parsed.get("header") if isinstance(parsed.get("header"), dict) else {}
        totals = parsed.get("totals") if isinstance(parsed.get("totals"), dict) else {}
        gross = float(totals.get("invoice_total") or totals.get("line_total") or 0)
        taxable = float(totals.get("taxable_amount") or totals.get("taxable") or 0)
        if gross <= 0:
            continue
        so = str(header.get("order_ref_no") or ref or "").strip()
        out.append(
            {
                "tracking_id": tid,
                "distributor_id": dist_id,
                "invoice_no": header.get("invoice_no"),
                "invoice_date": header.get("invoice_date"),
                "so_number": matchdb.normalize_so_number(so) or so or None,
                "qty": totals.get("qty"),
                "gross": round(gross, 2),
                "taxable": round(taxable, 2),
            }
        )
    return out


def _allocate(deposits: list[dict[str, Any]], cis: list[dict[str, Any]]) -> float:
    """Apply deposits to CIs oldest-first; return money left over (advance)."""
    pool = sorted(
        ({"date": d.get("payment_date") or "", "left": float(d.get("amount") or 0)} for d in deposits),
        key=lambda d: d["date"],
    )
    for ci in sorted(cis, key=lambda c: (c.get("invoice_date") or "", c.get("invoice_no") or "")):
        need = ci["net"]
        covered = 0.0
        for dep in pool:
            if need - covered <= 0.005:
                break
            take = min(dep["left"], need - covered)
            if take > 0:
                dep["left"] -= take
                covered += take
        ci["covered"] = round(covered, 2)
        ci["uncovered"] = round(max(0.0, need - covered), 2)
        ci["status"] = "paid" if ci["uncovered"] <= _TOL else ("part_paid" if covered > _TOL else "unpaid")
    return round(sum(d["left"] for d in pool), 2)


def build_ledger(
    db,
    conn: sqlite3.Connection,
    *,
    user_id: int,
    workspace_id: str,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    status = db.list_distributor_category_payment_status(user_id) or []
    slots = _so_slots(conn, user_id)
    pending = matchdb.list_pending_so_for_user(conn, user_id)
    try:
        from app.services import so_mail_import as smi

        due = smi.due_dates_for_user(conn, user_id)
    except Exception:
        due = {}

    # CIs grouped by (distributor, season, category); unmapped kept per distributor.
    ci_by_slot: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    unlinked: dict[int, list[dict[str, Any]]] = {}
    for ci in _ci_rows(conn, workspace_id):
        slot = slots.get(ci["so_number"] or "")
        if slot:
            ci_by_slot.setdefault(slot, []).append(ci)
        elif ci.get("distributor_id") is not None:
            unlinked.setdefault(int(ci["distributor_id"]), []).append(ci)

    pending_by_slot: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for p in pending:
        if p.get("distributor_id") is None:
            continue
        key = (int(p["distributor_id"]), p.get("season") or "", p.get("category") or "")
        pending_by_slot.setdefault(key, []).append(
            {"so_number": p["so_number"], "status": p["status"], "pending_qty": p["pending_qty"],
             "pending_net": p["pending_net"]}
        )

    distributors = []
    totals_all = {"order_payable": 0.0, "received": 0.0, "billed": 0.0}
    for d in status:
        did = int(d.get("distributor_id") or 0)
        cats_out = []
        alerts: list[dict[str, Any]] = []
        for s in d.get("seasons") or []:
            season = s.get("season") or ""
            for c in s.get("categories") or []:
                category = c.get("category") or ""
                cd_pct = float(c.get("cd_percent") or 0)
                tds_pct = float(c.get("tds_percent") or 0) if c.get("tds_applicable") else 0.0
                cis = []
                for ci in ci_by_slot.get((did, season, category), []):
                    cd = round(ci["taxable"] * cd_pct / 100.0, 2)
                    tds = round(ci["gross"] * tds_pct / 100.0, 2)
                    cis.append({**ci, "cd": cd, "tds": tds, "net": round(ci["gross"] - cd - tds, 2)})
                order_payable = round(float(c.get("bill_after_cd") or 0) - float(c.get("tds_amount") or 0), 2)
                received = round(float(c.get("paid_total") or 0), 2)
                billed = round(sum(ci["net"] for ci in cis), 2)
                advance_left = _allocate(c.get("deposits") or [], cis)
                unbilled_advance = round(received - billed, 2)
                payment_due = round(order_payable - received, 2)
                due_info = due.get((did, season, category)) or {}
                row = {
                    "season": season,
                    "category": category,
                    "cd_percent": cd_pct,
                    "tds_percent": tds_pct,
                    "order_gross": c.get("so_total"),
                    "order_payable": order_payable,
                    "received": received,
                    "billed": billed,
                    "ci_count": len(cis),
                    "unbilled_advance": unbilled_advance,
                    "payment_due": payment_due,
                    "advance_not_used_by_ci": advance_left,
                    "due_date": due_info.get("due_date"),
                    "deposits": sorted(c.get("deposits") or [], key=lambda x: x.get("payment_date") or ""),
                    "cis": sorted(cis, key=lambda x: (x.get("invoice_date") or "", x.get("invoice_no") or "")),
                    "pending_sos": sorted(pending_by_slot.get((did, season, category), []),
                                          key=lambda x: -x["pending_net"]),
                }
                cats_out.append(row)
                label = f"{category} {season}".strip()
                if unbilled_advance > _TOL and not cis:
                    alerts.append({"level": "warn", "text": f"{label}: received {unbilled_advance:,.0f} but no CI raised yet"})
                elif unbilled_advance > _TOL:
                    alerts.append({"level": "info", "text": f"{label}: {unbilled_advance:,.0f} received, CI still to be raised"})
                elif unbilled_advance < -_TOL:
                    alerts.append({"level": "warn", "text": f"{label}: billed {-unbilled_advance:,.0f} more than received"})
                if payment_due < -_TOL:
                    alerts.append({"level": "info", "text": f"{label}: distributor paid {-payment_due:,.0f} extra"})
                elif payment_due > _TOL and due_info.get("due_date") and due_info["due_date"] < today.isoformat():
                    alerts.append({"level": "alert", "text": f"{label}: {payment_due:,.0f} overdue since {due_info['due_date']}"})
        tot = {k: round(sum(r[k] for r in cats_out), 2)
               for k in ("order_payable", "received", "billed", "unbilled_advance", "payment_due")}
        for k in ("order_payable", "received", "billed"):
            totals_all[k] += tot[k]
        extra = unlinked.get(did) or []
        if extra:
            alerts.append({"level": "info", "text": f"{len(extra)} CI not linked to a matched SO (check Order Desk)"})
        distributors.append(
            {
                "distributor_id": did,
                "distributor_name": d.get("distributor_name"),
                "totals": tot,
                "categories": cats_out,
                "unlinked_cis": [{k: ci[k] for k in ("invoice_no", "invoice_date", "so_number", "gross")}
                                 for ci in extra],
                "alerts": alerts,
            }
        )
    distributors.sort(key=lambda x: -abs(x["totals"]["payment_due"]) - abs(x["totals"]["unbilled_advance"]))
    totals_all = {k: round(v, 2) for k, v in totals_all.items()}
    totals_all["unbilled_advance"] = round(totals_all["received"] - totals_all["billed"], 2)
    totals_all["payment_due"] = round(totals_all["order_payable"] - totals_all["received"], 2)
    return {"distributors": distributors, "totals": totals_all, "as_of": today.isoformat()}

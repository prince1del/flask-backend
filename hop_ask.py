"""Ask NEXORA — House of Prizm answers (workspace-isolated).

Always filters by workspace_id. Never reads Bombay Dyeing / other-workspace tables.
Brand UI remains "Ask NEXORA"; data plane is hop_* only for house_of_prizm.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from typing import Any

from app.hop_schema import HOP_WORKSPACE_ID, ensure_hop_schema
from app import hop_ops


def _money(n: float | int | None) -> str:
    try:
        return f"₹{float(n or 0):,.0f}"
    except (TypeError, ValueError):
        return "₹0"


def _require_hop_workspace(workspace_id: str | None) -> str:
    ws = (workspace_id or "").strip()
    if ws != HOP_WORKSPACE_ID:
        raise PermissionError("Ask NEXORA (Prizm data) is only available in the House of Prizm workspace")
    return ws


def _time_greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def _help(lang: str = "en") -> str:
    if lang == "hi":
        return (
            f"{_time_greeting()} — main kaise madad karun?\n\n"
            "Try asking:\n"
            "• Quotations pending follow-up dikhao\n"
            "• Highest probability hotel projects\n"
            "• Overdue payments 30 days\n"
            "• Today's meetings\n"
            "• Sales funnel summary\n"
            "• Vendor comparison for a project"
        )
    return (
        f"{_time_greeting()} — how may I help you?\n\n"
        "Try asking:\n"
        "• Show quotations pending follow-up\n"
        "• Which hotel projects have the highest probability?\n"
        "• Show payments overdue by more than 30 days\n"
        "• Prepare today's sales report\n"
        "• Lead pipeline summary\n"
        "• Which customers haven't ordered in 6 months?"
    )


def _greeting_answer() -> str:
    return f"{_time_greeting()} — how may I help you?"


def _lang(q: str) -> str:
    lower = (q or "").lower()
    if re.search(r"[\u0900-\u097F]", q or "") or any(
        w in lower for w in ("kitna", "dikhao", "batao", "kaun", "hai", "mein")
    ):
        return "hi"
    return "en"


def answer_question(
    conn: sqlite3.Connection,
    user_id: int,
    question: str,
    workspace_id: str | None = None,
    db_path: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Answer using hop_* tables only. workspace_id must be house_of_prizm."""
    ws = _require_hop_workspace(workspace_id)
    if db_path:
        ensure_hop_schema(db_path)
    lang = _lang(question)
    q = re.sub(r"\s+", " ", (question or "").strip())
    q_lower = q.lower()
    if q_lower in {"hi", "hello", "hey", "namaste", "good morning", "good afternoon", "good evening"}:
        return {"answer": _greeting_answer(), "intent": "greeting", "data": {"workspace_id": ws, "user_id": user_id}}
    if not q or q_lower in {"help", "?"}:
        return {"answer": _help(lang), "intent": "help", "data": {"workspace_id": ws, "user_id": user_id}}

    lower = q.lower()

    # --- Quotations pending ---
    if ("quotation" in lower or "quote" in lower) and any(
        x in lower for x in ("pending", "follow", "pending follow", "follow-up", "follow up")
    ):
        kpis = hop_ops.report_quotation_kpis(conn, ws)
        pending = [r for r in (kpis.get("rows") or []) if (r.get("status") or "").lower() in ("draft", "pending", "pending_approval", "sent", "follow_up", "negotiation")]
        follow = [r for r in pending if (r.get("status") or "").lower() in ("sent", "follow_up", "negotiation", "pending")]
        rows = follow or pending
        if not rows:
            return {
                "answer": "No quotations pending follow-up in your workspace.",
                "intent": "quotations_pending",
                "data": {"workspace_id": ws, "count": 0},
            }
        lines = [
            f"• **{r.get('quote_no')}** v{r.get('version')} — {r.get('customer_company') or '—'} / {r.get('project_name') or '—'} · {_money(r.get('value'))} · status `{r.get('status')}`"
            for r in rows[:15]
        ]
        return {
            "answer": f"**{len(rows)}** quotation(s) need attention:\n" + "\n".join(lines),
            "intent": "quotations_pending",
            "data": {"workspace_id": ws, "count": len(rows), "rows": rows[:15]},
        }

    # --- High probability projects ---
    if ("probability" in lower or "probabilit" in lower) and any(
        x in lower for x in ("high", "highest", "hotel", "project", "top")
    ):
        from app.hop_db import list_projects

        projects = list_projects(conn, ws)
        ranked = sorted(projects, key=lambda p: float(p.get("probability_pct") or 0), reverse=True)
        top = [p for p in ranked if float(p.get("probability_pct") or 0) > 0][:10] or ranked[:10]
        if not top:
            return {
                "answer": "No projects in your workspace yet.",
                "intent": "high_probability_projects",
                "data": {"workspace_id": ws, "count": 0},
            }
        lines = [
            f"• **{p.get('project_name')}** — {p.get('customer_company') or p.get('hotel_name') or '—'} · prob **{p.get('probability_pct') or 0}%** · stage `{p.get('stage')}` · {_money(p.get('project_value') or p.get('expected_value'))}"
            for p in top
        ]
        return {
            "answer": "Highest probability projects (your workspace):\n" + "\n".join(lines),
            "intent": "high_probability_projects",
            "data": {"workspace_id": ws, "rows": top},
        }

    # --- Overdue payments / receivables ---
    if any(x in lower for x in ("overdue", "outstanding", "receivable", "ageing", "aging")) or (
        "payment" in lower and any(x in lower for x in ("30", "overdue", "due", "pending"))
    ):
        recv = hop_ops.report_receivables(conn, ws)
        ageing = recv.get("ageing") or {}
        top = recv.get("top_customers") or []
        invoices = recv.get("invoices") or []
        # filter 30+ if asked
        want_30 = "30" in lower or "overdue" in lower
        lines = [
            f"Ageing — 0–30: {_money(ageing.get('0_30'))}, 31–60: {_money(ageing.get('31_60'))}, "
            f"61–90: {_money(ageing.get('61_90'))}, 90+: {_money(ageing.get('90_plus'))}"
        ]
        if top:
            lines.append("Top outstanding:")
            for c in top[:8]:
                lines.append(f"• {c.get('customer')}: {_money(c.get('outstanding'))}")
        if want_30 and invoices:
            lines.append("Open invoices:")
            for inv in invoices[:10]:
                lines.append(
                    f"• {inv.get('invoice_no')} — {inv.get('customer_company')} · bal {_money(inv.get('balance'))} · due {inv.get('due_date') or '—'}"
                )
        if not top and not invoices:
            return {
                "answer": "No outstanding receivables in your workspace.",
                "intent": "receivables",
                "data": {"workspace_id": ws, "ageing": ageing},
            }
        return {
            "answer": "\n".join(lines),
            "intent": "receivables",
            "data": {"workspace_id": ws, "ageing": ageing, "top_customers": top[:8]},
        }

    # --- Meetings today ---
    if "meeting" in lower and any(x in lower for x in ("today", "aaj", "upcoming", "follow")):
        dash = hop_ops.report_meetings_dashboard(conn, ws)
        counts = dash.get("counts") or {}
        today = dash.get("today") or []
        lines = [
            f"Meetings today: **{counts.get('today', 0)}**, upcoming: **{counts.get('upcoming', 0)}**, "
            f"missed: **{counts.get('missed', 0)}**, follow-up due: **{counts.get('follow_up_due', 0)}**"
        ]
        for m in today[:10]:
            lines.append(
                f"• {(m.get('scheduled_at') or '')[:16]} — {m.get('title')} · {m.get('customer_company') or m.get('project_name') or '—'}"
            )
        return {
            "answer": "\n".join(lines),
            "intent": "meetings_dashboard",
            "data": {"workspace_id": ws, "counts": counts},
        }

    # --- Daily / today's sales report ---
    if any(x in lower for x in ("today", "aaj", "daily")) and any(
        x in lower for x in ("report", "sales", "activity", "summary", "prepare")
    ):
        daily = hop_ops.report_daily_activity(conn, ws)
        return {
            "answer": (
                f"**Daily activity** ({daily.get('day')}):\n"
                f"• Leads: {daily.get('leads_created')}\n"
                f"• Meetings: {daily.get('meetings')}\n"
                f"• Samples sent: {daily.get('samples_sent')}\n"
                f"• Follow-ups: {daily.get('follow_ups')}\n"
                f"• Quotes: {daily.get('quotes_sent')}\n"
                f"• Orders closed: {daily.get('orders_closed')}\n"
                f"• Collections: {_money(daily.get('collections'))}"
            ),
            "intent": "daily_activity",
            "data": {"workspace_id": ws, **daily},
        }

    # --- Lead pipeline ---
    if "pipeline" in lower or ("lead" in lower and any(x in lower for x in ("stage", "funnel", "summary", "count"))):
        pipe = hop_ops.report_lead_pipeline(conn, ws)
        stages = [s for s in (pipe.get("stages") or []) if s.get("count")]
        kpis = pipe.get("kpis") or {}
        lines = [f"• `{s['stage']}`: {s['count']} · {_money(s['value'])}" for s in stages] or ["No leads yet."]
        return {
            "answer": (
                f"Lead pipeline — conversion **{kpis.get('conversion_rate_pct')}%**, "
                f"win ratio **{kpis.get('win_ratio_pct')}%**\n" + "\n".join(lines)
            ),
            "intent": "lead_pipeline",
            "data": {"workspace_id": ws, **pipe},
        }

    # --- Sales funnel ---
    if "funnel" in lower:
        funnel = hop_ops.report_funnel(conn, ws)
        active = [s for s in funnel if s.get("count")]
        lines = [f"• `{s['stage']}`: {s['count']} · {_money(s['value'])}" for s in active] or ["No projects in funnel yet."]
        return {
            "answer": "Project sales funnel (your workspace):\n" + "\n".join(lines),
            "intent": "funnel",
            "data": {"workspace_id": ws, "stages": funnel},
        }

    # --- Repeat / inactive customers ---
    if any(x in lower for x in ("haven't ordered", "have not ordered", "not ordered", "6 month", "180", "inactive", "repeat")):
        buckets = hop_ops.report_repeat_business(conn, ws)
        key = "180" if ("6 month" in lower or "180" in lower or "6 months" in lower) else "90"
        rows = buckets.get(key) or buckets.get("180") or []
        if not rows:
            return {
                "answer": "No inactive customers matched in your workspace.",
                "intent": "repeat_business",
                "data": {"workspace_id": ws, "bucket": key, "count": 0},
            }
        lines = [
            f"• {c.get('company')} — last purchase {c.get('last_purchase') or 'never'} · outstanding {_money(c.get('outstanding'))}"
            for c in rows[:15]
        ]
        return {
            "answer": f"Customers quiet ~{key}+ days (or never ordered):\n" + "\n".join(lines),
            "intent": "repeat_business",
            "data": {"workspace_id": ws, "bucket": key, "rows": rows[:15]},
        }

    # --- Vendor / price / rate matrix ---
    if (
        "vendor" in lower
        or "supplier" in lower
        or "cheapest" in lower
        or "rate compare" in lower
        or ("best" in lower and "price" in lower)
        or ("price" in lower and "fabric" in lower)
        or "fr fabric" in lower
    ):
        matrix = hop_ops.rate_comparison_matrix(conn, ws)
        suggestions = matrix.get("suggestions") or []
        if suggestions and any(x in lower for x in ("compar", "best", "cheap", "rate", "sasta", "lowest")):
            lines = [
                f"• **{s['label']}** → {s['best_supplier']} @ ₹{s['best_landed']:,.0f} landed"
                for s in suggestions[:12]
            ]
            return {
                "answer": "Cheapest supplier by product (landed = rate + GST):\n" + "\n".join(lines),
                "intent": "vendor_comparison",
                "data": {"workspace_id": ws, "suggestions": suggestions[:12], "summary": matrix.get("summary")},
            }

        vendors = hop_ops.list_vendors(conn, ws)
        cmps = hop_ops.list_vendor_comparisons(conn, ws)
        if "compar" in lower or "best" in lower or "fr" in lower:
            ranked = sorted(cmps, key=lambda r: float(r.get("rate") or 1e18))
            if not ranked:
                if suggestions:
                    lines = [
                        f"• **{s['label']}** → {s['best_supplier']} @ ₹{s['best_landed']:,.0f}"
                        for s in suggestions[:10]
                    ]
                    return {
                        "answer": "Rate matrix suggestions:\n" + "\n".join(lines),
                        "intent": "vendor_comparison",
                        "data": {"workspace_id": ws, "suggestions": suggestions[:10]},
                    }
                if not vendors:
                    return {
                        "answer": "No vendors or comparisons in your workspace yet.",
                        "intent": "vendors",
                        "data": {"workspace_id": ws},
                    }
                lines = [
                    f"• **{v.get('company')}** — {v.get('products') or '—'} · lead {v.get('lead_time_days') or '—'}d · rating {v.get('rating') or '—'}"
                    for v in vendors[:12]
                ]
                return {
                    "answer": "Vendors in your workspace:\n" + "\n".join(lines),
                    "intent": "vendors",
                    "data": {"workspace_id": ws, "vendors": vendors[:12]},
                }
            lines = [
                f"• **{r.get('vendor_company')}** — {r.get('product_name')} @ {_money(r.get('rate'))} · MOQ {r.get('moq') or '—'}{' ★ winner' if r.get('is_winner') else ''}"
                for r in ranked[:12]
            ]
            return {
                "answer": "Vendor comparisons (best rate first):\n" + "\n".join(lines),
                "intent": "vendor_comparison",
                "data": {"workspace_id": ws, "rows": ranked[:12]},
            }
        lines = [f"• **{v.get('company')}** — {v.get('products') or '—'}" for v in vendors[:15]]
        return {
            "answer": ("Vendors:\n" + "\n".join(lines)) if lines else "No vendors yet.",
            "intent": "vendors",
            "data": {"workspace_id": ws},
        }

    # --- Project by name hint ---
    m = re.search(r"(?:project|hotel)\s+([a-z0-9][a-z0-9\s\-]{2,40})", lower)
    name_hint = None
    if m:
        name_hint = m.group(1).strip()
    elif "holiday inn" in lower:
        name_hint = "holiday inn"
    if name_hint:
        from app.hop_db import list_projects

        projects = list_projects(conn, ws, q=name_hint)
        if projects:
            p = projects[0]
            hub = hop_ops.get_project_hub(conn, ws, int(p["id"])) or {}
            return {
                "answer": (
                    f"**{p.get('project_name')}** · stage `{p.get('stage')}` · "
                    f"{_money(p.get('project_value') or p.get('expected_value'))} · "
                    f"quotes {len(hub.get('quotations') or [])}, orders {len(hub.get('orders') or [])}, "
                    f"invoices {len(hub.get('invoices') or [])}"
                ),
                "intent": "project_lookup",
                "data": {"workspace_id": ws, "project": p},
            }

    # --- Profit snapshot ---
    if "profit" in lower or "margin" in lower:
        profit = hop_ops.report_profitability(conn, ws)
        return {
            "answer": (
                f"Revenue {_money(profit.get('revenue'))} · COGS {_money(profit.get('cogs'))} · "
                f"Gross profit {_money(profit.get('gross_profit'))} · "
                f"Margin {profit.get('gross_margin_pct') if profit.get('gross_margin_pct') is not None else 'N/A'}%"
            ),
            "intent": "profitability",
            "data": {"workspace_id": ws, **profit},
        }

    # --- Executive snapshot fallback ---
    if any(x in lower for x in ("dashboard", "snapshot", "overview", "summary", "kaisa chal")):
        from app.hop_db import list_leads, list_meetings
        from app.hop_db import list_projects as _lp

        return {
            "answer": (
                f"Your Prizm workspace snapshot:\n"
                f"• Customers / Projects / Leads are live in the sidebar modules\n"
                f"• Projects: {len(_lp(conn, ws))}, Leads: {len(list_leads(conn, ws))}, "
                f"Meetings: {len(list_meetings(conn, ws))}\n"
                f"Ask about quotations, pipeline, receivables, or a project name."
            ),
            "intent": "snapshot",
            "data": {"workspace_id": ws},
        }

    return {
        "answer": _help(lang) + f"\n\nI couldn't match: “{q}”.",
        "intent": "unknown",
        "data": {"workspace_id": ws, "user_id": user_id},
    }


HOP_ASK_EXAMPLES = [
    "Show quotations pending follow-up",
    "Which hotel projects have the highest probability?",
    "Show payments overdue by more than 30 days",
    "Prepare today's sales report",
    "Lead pipeline summary",
    "Which customers haven't ordered in 6 months?",
    "Which vendor has the best price?",
    "Sales funnel summary",
]

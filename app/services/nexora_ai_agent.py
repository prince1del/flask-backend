"""Nexora AI agent — Gemini function-calling over the BD workspace's own data.

Covers Order Desk (CI/SO reconciliation), Target vs Achievement, PJP (planned
visits), and Distributor Payment Status — the same domains the app's own My
Day / Order Desk / Distributor Zone screens already show, just reachable by
free-form question instead of navigating there. Reuses the same raw-REST
Gemini call pattern as app/services/visiting_card_ocr.py (no new SDK
dependency, same GEMINI_API_KEY already configured for card OCR).

Tool functions are bound server-side to the authenticated user_id/workspace_id
— Gemini is never given these as editable arguments, so results stay isolated
exactly like the existing REST endpoints for the same data already are.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from app.services.gemini_models import get_ocr_gemini_models
from app.services.visiting_card_ocr import _gemini_key

MAX_TOOL_ROUNDS = 4
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


def _system_prompt() -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    return f"""You are Nexora's assistant for a textile B2B sales exec (Bombay Dyeing
workspace). Today's date is {today} (UTC) — use this to resolve relative dates
("tomorrow", "kal", "next Monday") into an actual YYYY-MM-DD before calling
get_pjp_for_date.

You answer questions across four areas, each with its own tools:
- Order Desk: CI (customer indent) vs SO (sales order) reconciliation —
  matched/mismatched quantities and values, by season, category
  (Bed/Bath/Pillow/TOB), and distributor.
- Target vs Achievement: this fiscal year's (or a named FY's) sales target
  and how much has been achieved, company-wide or per distributor.
- PJP (Permanent Journey Plan): the planned visit for a specific date.
- Distributor Payment Status: SO bill amount, deposits recorded, and
  outstanding balance per distributor/season/category.

Rules:
- ONLY state numbers/facts that came back from a tool call. Never estimate or invent.
- If a tool returns no data, say so plainly - do not guess.
- Keep answers short and direct, in the same language style as the question
  (Hindi/Hinglish questions get Hindi/Hinglish answers).
- When useful, mention counts (e.g. "3 distributors have QTY_MISMATCH this season")
  rather than dumping raw rows.
- You are speaking to the company's founder. In Hindi/Hinglish, always use the
  respectful "aap" form (poochiye/bataiye/dekhiye), never the casual "tu/tum"
  imperative (pooch/bata/dekh) — that reads as rude, not helpful.
"""


class NexoraAiAgentError(RuntimeError):
    """Raised for hard agent failures (no key, all Gemini models unreachable)."""


def _tool_declarations() -> list[dict[str, Any]]:
    return [
        {
            "name": "get_order_desk_overview",
            "description": (
                "Season-by-category overview of this user's saved filled orders - "
                "distributor totals (piece qty, ex-mill value) grouped by season and "
                "category (Bed/Bath/Pillow/TOB/...). Use for 'what does my order book "
                "look like' type questions."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "list_order_match_runs",
            "description": (
                "List this user's FO vs SO-pack match runs - each run has a "
                "distributor, category, season, and counts of matched/fuzzy-matched/"
                "qty-mismatched/value-mismatched/missing-on-SO/extra-on-SO lines. "
                "Use for 'which orders have mismatches' type questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "distributor_name": {
                        "type": "string",
                        "description": "Filter to runs whose distributor name contains this text (optional).",
                    },
                    "category": {
                        "type": "string",
                        "description": "Filter to this category, e.g. Bed, Bath, Pillow, TOB (optional).",
                    },
                    "season": {
                        "type": "string",
                        "description": "Filter to this season code, e.g. AW26 (optional).",
                    },
                },
            },
        },
        {
            "name": "get_order_match_detail",
            "description": (
                "Row-level brand/size detail (status per line: MATCH, MATCH_FUZZY_BRAND, "
                "QTY_MISMATCH, VALUE_MISMATCH, MISSING_ON_SO, EXTRA_ON_SO) for ONE match "
                "run. Use this after list_order_match_runs to explain WHY a specific run "
                "is mismatched."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "integer",
                        "description": "The match run id, from list_order_match_runs.",
                    }
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "get_target_vs_achievement",
            "description": (
                "This fiscal year's (or a named FY's) sales target and how much has "
                "been achieved so far - company-wide by default, or for one named "
                "distributor. Use for 'target kitna hai', 'achievement kitni hui', "
                "'kis FY ka target' type questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "financial_year": {
                        "type": "string",
                        "description": (
                            "e.g. '2025-2026' or '2025-26' (optional - defaults to "
                            "the most recent fiscal year on file)."
                        ),
                    },
                    "distributor_name": {
                        "type": "string",
                        "description": "Narrow to one distributor's target/achievement (optional).",
                    },
                },
            },
        },
        {
            "name": "get_pjp_for_date",
            "description": (
                "The planned visit (Permanent Journey Plan) for one specific date - "
                "place to visit, business activity, or holiday/leave. Resolve relative "
                "dates ('tomorrow', 'kal') to YYYY-MM-DD yourself using today's date "
                "from the system instructions before calling this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "YYYY-MM-DD.",
                    }
                },
                "required": ["date"],
            },
        },
        {
            "name": "get_distributor_payment_status",
            "description": (
                "SO bill amount, deposits recorded, and outstanding balance per "
                "distributor/season/category, from matched Sales Orders. Use for "
                "'kiska payment pending hai', 'outstanding kitna hai' type questions. "
                "Omit distributor_name for the full list across all distributors."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "distributor_name": {
                        "type": "string",
                        "description": "Filter to one distributor (optional).",
                    }
                },
            },
        },
    ]


def _db_path() -> str:
    from flask import current_app

    return str(current_app.config.get("DATABASE_PATH") or "centralized_db.sqlite3")


def _sanitize(obj: Any) -> Any:
    """Drop non-JSON-safe values so json.dumps never chokes mid tool-loop."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return None
    return obj


def _run_tool(
    name: str, args: dict[str, Any], *, user_id: int, workspace_id: str
) -> dict[str, Any]:
    """Execute one tool call. user_id/workspace_id always come from the
    caller (JWT) — never from `args` — so Gemini cannot read another
    account's data."""
    import filled_orders_db as fodb
    from app.services import fo_so_match_db as matchdb

    conn = sqlite3.connect(_db_path())
    try:
        if name == "get_order_desk_overview":
            fodb.ensure_schema(conn)
            seasons = fodb.build_season_overview(conn, user_id)
            return {"seasons": _sanitize(seasons)}

        if name == "list_order_match_runs":
            runs = matchdb.list_match_runs(conn, user_id=user_id)
            distributor_name = str(args.get("distributor_name") or "").strip().lower()
            category = str(args.get("category") or "").strip().lower()
            season = str(args.get("season") or "").strip().lower()
            if distributor_name:
                runs = [
                    r for r in runs
                    if distributor_name in str(r.get("distributor_name") or "").lower()
                ]
            if category:
                runs = [r for r in runs if category == str(r.get("category") or "").lower()]
            if season:
                runs = [r for r in runs if season == str(r.get("season") or "").lower()]
            return {"runs": _sanitize(runs), "count": len(runs)}

        if name == "get_order_match_detail":
            run_id = args.get("run_id")
            if run_id is None:
                return {"error": "run_id is required"}
            run = matchdb.get_match_run(conn, int(run_id), user_id=user_id)
            if not run:
                return {"error": f"No match run {run_id} found for this user"}
            return {"run": _sanitize(run)}

        if name == "get_target_vs_achievement":
            from centralized_db_system.db import CentralizedDB

            db = CentralizedDB(_db_path())
            db.ensure_target_achievement_tables()
            conn.row_factory = sqlite3.Row
            fy_rows = conn.execute(
                "SELECT id, financial_year FROM target_achievement_years "
                "WHERE workspace_id = ? ORDER BY financial_year",
                (workspace_id,),
            ).fetchall()
            conn.row_factory = None
            fy_years = [(r["id"], r["financial_year"]) for r in fy_rows]
            requested_fy = str(args.get("financial_year") or "").strip()
            if requested_fy:
                matched = [
                    (yid, fy) for yid, fy in fy_years
                    if requested_fy in fy or fy in requested_fy
                ]
                fy_years = matched or (fy_years[-1:] if fy_years else [])
            elif fy_years:
                fy_years = fy_years[-1:]  # most recent FY on file
            if not fy_years:
                return {"error": "No target/achievement fiscal years found for this workspace"}

            distributor_name = str(args.get("distributor_name") or "").strip()
            dist = None
            if distributor_name:
                from app.routes.data import _find_distributor_fuzzy

                dist = _find_distributor_fuzzy(db, distributor_name, workspace_id)
                if not dist:
                    return {"error": f"No distributor matching '{distributor_name}' found"}

            results = []
            for year_id, fy_label in fy_years:
                breakup = db.list_target_distributor_breakup(workspace_id, year_id)
                if dist:
                    row = next(
                        (r for r in breakup if r.get("distributor_id") == dist.get("id")), None
                    )
                    if row:
                        results.append({
                            "fiscal_year": fy_label,
                            "distributor": row.get("distributor_name"),
                            "target_rs": float(row.get("target_lakhs") or 0) * 100_000,
                            "achieved_rs": float(row.get("achievement_lakhs") or 0) * 100_000,
                        })
                else:
                    total_target = sum(float(r.get("target_lakhs") or 0) for r in breakup) * 100_000
                    total_achieved = sum(
                        float(r.get("achievement_lakhs") or 0) for r in breakup
                    ) * 100_000
                    results.append({
                        "fiscal_year": fy_label,
                        "target_rs": total_target,
                        "achieved_rs": total_achieved,
                    })
            return {"results": _sanitize(results)}

        if name == "get_pjp_for_date":
            date_str = str(args.get("date") or "").strip()
            if not date_str:
                return {"error": "date is required (YYYY-MM-DD)"}
            try:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT place_to_visit, business_activity, particulars, day_type "
                    "FROM monthly_pjp_days WHERE workspace_id = ? AND user_id = ? "
                    "AND plan_date = ?",
                    (workspace_id, user_id, date_str),
                ).fetchone()
                conn.row_factory = None
            except sqlite3.OperationalError:
                row = None
            if not row:
                return {"date": date_str, "planned": False}
            return {"date": date_str, "planned": True, **{k: row[k] for k in row.keys()}}

        if name == "get_distributor_payment_status":
            from centralized_db_system.db import CentralizedDB

            db = CentralizedDB(_db_path())
            rows = db.list_distributor_category_payment_status(user_id)
            distributor_name = str(args.get("distributor_name") or "").strip().lower()
            if distributor_name:
                rows = [
                    r for r in rows
                    if distributor_name in str(r.get("distributor_name") or "").lower()
                ]
            # Trim deposit line-items to a count — the running totals already
            # carry paid/outstanding, individual payment notes aren't needed
            # for an answer and would just burn tokens.
            for r in rows:
                for season_entry in r.get("seasons", []):
                    for cat in season_entry.get("categories", []):
                        cat["deposits_count"] = len(cat.get("deposits") or [])
                        cat.pop("deposits", None)
            return {"distributors": _sanitize(rows), "count": len(rows)}

        return {"error": f"Unknown tool: {name}"}
    finally:
        conn.close()


def _call_gemini(contents: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    """Returns (response_json, model_used) — the model name is needed for usage logging."""
    key = _gemini_key()
    if not key:
        raise NexoraAiAgentError("GEMINI_API_KEY not configured on the server")

    body = {
        "systemInstruction": {"parts": [{"text": _system_prompt()}]},
        "contents": contents,
        "tools": [{"functionDeclarations": _tool_declarations()}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024},
    }
    payload = json.dumps(body).encode("utf-8")
    last_err = ""
    for model in get_ocr_gemini_models():
        url = _GEMINI_URL.format(model=model, key=key)
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8")), model
        except urllib.error.HTTPError as exc:
            try:
                last_err = exc.read().decode("utf-8", errors="ignore")[:300]
            except Exception:
                last_err = str(exc)
            if exc.code in (404, 429):
                # 404 = model unavailable, 429 = this model's quota is
                # exhausted — either way, the next model in the list might
                # still work (they're billed/rate-limited separately).
                continue
            raise NexoraAiAgentError(f"Gemini call failed: {last_err}") from exc
        except Exception as exc:  # noqa: BLE001 - network errors, try next model
            last_err = str(exc)
    raise NexoraAiAgentError(f"Gemini call failed: {last_err or 'no model available'}")


# --- Usage logging -----------------------------------------------------
# Token counts straight from Gemini's own usageMetadata, so the numbers match
# what Google actually bills — not an estimate. One row per ask_order_desk()
# call (summed across its internal tool-calling rounds).

def _ensure_usage_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_agent_usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            user_id INTEGER NOT NULL,
            feature TEXT NOT NULL,
            model TEXT,
            rounds INTEGER NOT NULL DEFAULT 1,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            query_preview TEXT
        )
        """
    )
    conn.commit()


def _log_usage(
    *,
    user_id: int,
    feature: str,
    model: str,
    rounds: int,
    prompt_tokens: int,
    output_tokens: int,
    total_tokens: int,
    query: str,
) -> None:
    try:
        conn = sqlite3.connect(_db_path())
        try:
            _ensure_usage_schema(conn)
            conn.execute(
                """
                INSERT INTO ai_agent_usage_log
                    (user_id, feature, model, rounds, prompt_tokens, output_tokens, total_tokens, query_preview)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, feature, model, rounds, prompt_tokens, output_tokens, total_tokens, query[:200]),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # usage logging must never break the actual answer


def get_usage_summary(*, user_id: int, all_users: bool, days: int = 30) -> dict[str, Any]:
    conn = sqlite3.connect(_db_path())
    try:
        _ensure_usage_schema(conn)
        where = "created_at >= datetime('now', ?)"
        params: list[Any] = [f"-{max(1, days)} days"]
        if not all_users:
            where += " AND user_id = ?"
            params.append(user_id)
        totals = conn.execute(
            f"""
            SELECT COUNT(*), COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(output_tokens),0),
                   COALESCE(SUM(total_tokens),0)
            FROM ai_agent_usage_log WHERE {where}
            """,
            params,
        ).fetchone()
        by_day = conn.execute(
            f"""
            SELECT date(created_at) AS day, COUNT(*), COALESCE(SUM(total_tokens),0)
            FROM ai_agent_usage_log WHERE {where}
            GROUP BY day ORDER BY day DESC
            """,
            params,
        ).fetchall()
        return {
            "days": days,
            "calls": totals[0] or 0,
            "prompt_tokens": totals[1] or 0,
            "output_tokens": totals[2] or 0,
            "total_tokens": totals[3] or 0,
            "by_day": [
                {"day": r[0], "calls": r[1], "total_tokens": r[2]} for r in by_day
            ],
        }
    finally:
        conn.close()


def ask_order_desk(query: str, *, user_id: int, workspace_id: str) -> str:
    """Run the tool-calling loop for one question (Order Desk, Target vs
    Achievement, PJP, or Distributor Payment Status) and return the final
    text answer. Raises NexoraAiAgentError on hard failure."""
    contents: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": query}]}]
    rounds = 0
    prompt_tokens = 0
    output_tokens = 0
    model_used = ""

    def _finish(answer: str) -> str:
        _log_usage(
            user_id=user_id,
            feature="nexora_agent",
            model=model_used,
            rounds=rounds,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            total_tokens=prompt_tokens + output_tokens,
            query=query,
        )
        return answer

    for _ in range(MAX_TOOL_ROUNDS):
        rounds += 1
        data, model_used = _call_gemini(contents)
        usage = data.get("usageMetadata") or {}
        prompt_tokens += int(usage.get("promptTokenCount") or 0)
        output_tokens += int(usage.get("candidatesTokenCount") or 0)

        candidates = data.get("candidates") or []
        if not candidates:
            return _finish("Abhi AI se jawab nahi mil paaya — kripya thodi der baad dobara poochiye.")

        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []

        function_calls = [
            p["functionCall"] for p in parts if isinstance(p, dict) and "functionCall" in p
        ]
        text_parts = [p["text"] for p in parts if isinstance(p, dict) and p.get("text")]

        if not function_calls:
            return _finish("\n".join(text_parts).strip() or "Iska jawab abhi nahi mil paaya.")

        # Model's turn (including its function-call request) joins the history first.
        contents.append({"role": "model", "parts": parts})

        response_parts = [
            {
                "functionResponse": {
                    "name": call.get("name") or "",
                    "response": _run_tool(
                        call.get("name") or "", call.get("args") or {},
                        user_id=user_id, workspace_id=workspace_id,
                    ),
                }
            }
            for call in function_calls
        ]
        contents.append({"role": "function", "parts": response_parts})

    return _finish(
        "Yeh sawaal thoda complex tha — kripya thoda specific poochiye "
        "(jaise ek season ya category ka naam bata kar)."
    )

"""Nexora AI agent — Gemini function-calling over the BD workspace's own data.

Covers Order Desk (CI/SO reconciliation), Target vs Achievement, PJP (planned
visits), Distributor Payment Status, Party Profile, Article Price, Market
Visit (DSR), To-Do, Grievances, and Distributor Zone — the same domains the
app's own screens already show, just reachable by free-form question instead
of navigating there. Reuses the same raw-REST Gemini call pattern as
app/services/visiting_card_ocr.py (no new SDK dependency, same
GEMINI_API_KEY already configured for card OCR).

Tool functions are bound server-side to the authenticated user_id/workspace_id
— Gemini is never given these as editable arguments, so results stay isolated
exactly like the existing REST endpoints for the same data already are.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from app.services.gemini_models import get_ocr_gemini_models
from app.services.visiting_card_ocr import _gemini_key

MAX_TOOL_ROUNDS = 5
_AGENT_TIMEOUT_S = 60
_AGENT_FIRST_TRY_TIMEOUT_S = 25
# Fast model first for the agent; the OCR list (GEMINI_FLASH_MODEL etc.) follows
# as fallback. Override with GEMINI_AGENT_MODELS="model-a,model-b".
_AGENT_DEFAULT_MODELS = ("gemini-3.6-flash",)


def agent_gemini_models() -> tuple[str, ...]:
    csv_models = (os.getenv("GEMINI_AGENT_MODELS") or "").strip()
    first = tuple(m.strip() for m in csv_models.split(",") if m.strip()) or _AGENT_DEFAULT_MODELS
    out: list[str] = []
    for m in (*first, *get_ocr_gemini_models()):
        if m not in out:
            out.append(m)
    return tuple(out)
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


def _system_prompt() -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    return f"""You are Nexora's assistant for a textile B2B sales exec (Bombay Dyeing
workspace). Today's date is {today} (UTC) — use this to resolve relative dates
("tomorrow", "kal", "next Monday") into an actual YYYY-MM-DD before calling
get_pjp_for_date.

Words: SO = Sales Order (what the distributor ordered, from Bombay Dyeing).
CI = Commercial Invoice (the bill raised against an SO). FO = Filled Order sheet.

You answer questions across these areas, each with its own tools:
- Distributor Hisaab: per distributor, season and category (Bed/Bath/Pillow/
  TOB) - how many CIs, each CI's number/date/qty/value and whether payment has
  covered it (paid / part paid / unpaid), money received, and which SOs are
  still not billed by a CI. Use get_distributor_hisaab.
- Pending SO: Sales Orders not yet (fully) billed by a CI, across all
  distributors or one. Use get_pending_so.
- Order Desk: FO vs SO reconciliation —
  matched/mismatched quantities and values, by season, category
  (Bed/Bath/Pillow/TOB), and distributor.
- Target vs Achievement: this fiscal year's (or a named FY's) sales target
  and how much has been achieved, company-wide or per distributor.
- PJP (Permanent Journey Plan): the planned visit for a specific date.
- Distributor Payment Status: SO bill amount, deposits recorded, and
  outstanding balance per distributor/season/category.
- Party Profile: a distributor or retailer's contact person, phone, address,
  GST, email, and last visit date.
- Article Price: MRP/PTR/ex-mill price lookup by brand, product, or print
  style, optionally narrowed to one size.
- Market Visit (DSR): logged field visits — retailer/customer name,
  location, order value, feedback/remarks, by date or date range.
- To-Do: this user's personal task list — title, category, priority,
  status, due date.
- Grievances: distributor complaints logged, their status (open/resolved),
  and solution text.
- Distributor Zone: one distributor's combined snapshot — this FY's target
  vs achievement, plus their most recent secondary-sale months.

Rules:
- ONLY state numbers/facts that came back from a tool call. Never estimate or invent.
- If a tool returns no data, say so plainly - do not guess.
- Think for yourself: work out what the user wants and pick the tools. If no
  specific tool fits, use read_app to read the matching screen (it covers the
  whole app), or search_nexora to find the party/article/order first. If the question is incomplete ("MRP of"), ask one short
  question back (e.g. "Kis article ka MRP chahiye?") instead of giving up.
- Follow-ups: earlier turns of this conversation come before the latest
  question. If the latest question names no distributor, item, season or
  category of its own ("aur Bath ke?", "uska payment?", "kitne pcs?"), it is
  about the same ones as the earlier turns - keep them and call the tools
  again for fresh numbers. As soon as the user names a different distributor
  or item, switch to that one.
- Changes (add a to-do, record a payment, set CD/TDS, log a grievance, plan a
  day...): you cannot save anything yourself. Call propose_action with the exact
  fields, then reply with the summary and ask the user to tap Confirm or say
  "haan". Never say it is saved. If an id or amount is unclear, ask first.
- Always reply in Roman-script Hinglish (or English), never in Devanagari.
- "CI pending" is ambiguous. Answer both, clearly separated: (a) CIs raised
  but not yet paid (status unpaid / part paid), and (b) SOs still waiting for
  a CI (pending SO). Split counts by category (Bed / Bath / ...) when the
  question asks for it.
- ANSWER ONLY WHAT WAS ASKED. One or two short lines, no headings, no extra
  sections, no "details" the user did not ask for. The tools return a lot of
  data - pick the one fact asked and leave the rest out.
  Example: "Sain International ka last deposit kitna tha?" ->
  "Sain International ka last deposit ₹1,00,000 tha, 8 Oct 2026 ko (AW26 Bed)."
  NOT the whole hisaab, CI list or pending SOs. The user will ask a follow-up if
  they want more. Use a short list only when the question asks for a list or a
  split ("kitne Bed, kitne Bath").
- Same language style as the question (Hinglish questions get Hinglish answers).
- When useful, mention counts (e.g. "3 distributors have QTY_MISMATCH this season")
  rather than dumping raw rows.
- You are speaking to the company's founder. In Hindi/Hinglish, always use the
  respectful "aap" form (poochiye/bataiye/dekhiye), never the casual "tu/tum"
  imperative (pooch/bata/dekh) — that reads as rude, not helpful.
"""


class NexoraAiAgentError(RuntimeError):
    """Raised for hard agent failures (no key, all Gemini models unreachable)."""


def _read_app_declaration() -> dict[str, Any]:
    from app.services.nexora_app_reader import catalog_text

    try:
        from flask import current_app

        catalog = catalog_text(current_app._get_current_object())
    except Exception:  # no app context (unit tests)
        catalog = catalog_text(None)
    return {
        "name": "read_app",
        "description": (
            "Read any NEXORA screen's data, exactly as this user sees it in the app. Use it "
            "when no specific tool covers the question (mail imports, call lists, catalogues, "
            "secondary sales, monthly targets, article price history, users, reports...). "
            "Paths ({id} = a number from another reply):\n" + catalog
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "One of the listed paths with ids filled in."},
                "query": {
                    "type": "object",
                    "description": "Optional query parameters, e.g. {\"q\": \"aster\"} or {\"distributor_id\": \"9\"}.",
                },
            },
            "required": ["path"],
        },
    }


def _propose_action_declaration() -> dict[str, Any]:
    from app.services.nexora_actions import catalog_text

    return {
        "name": "propose_action",
        "description": (
            "Prepare a change in NEXORA for the user to confirm (nothing is saved by this "
            "call). Look up ids first with read_app/search (distributor_id, todo_id, "
            "tracking_id...). Write `summary` as one short line the user can check, with the "
            "real names, amounts and dates. Actions:\n" + catalog_text()
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "One of the listed action names."},
                "params": {"type": "object", "description": "Fields for that action."},
                "summary": {"type": "string", "description": "e.g. 'Kalra Agencies · AW26 Bed · ₹50,000 received on 13 Nov 2026 (NEFT)'"},
            },
            "required": ["action", "params", "summary"],
        },
    }


def _tool_declarations() -> list[dict[str, Any]]:
    return [
        _read_app_declaration(),
        _propose_action_declaration(),
        {
            "name": "get_distributor_hisaab",
            "description": (
                "Distributor Hisaab per season and category: CI count and each CI "
                "(invoice no, date, SO, qty, gross, net after CD/TDS, amount covered "
                "by payments, status paid/part_paid/unpaid), money received, order "
                "payable, still to collect, money received but CI not made, and SOs "
                "still not billed by a CI. Use for 'X ke kitne CI hai', 'kitne CI "
                "pending/unpaid hai', 'Bed ke kitne, Bath ke kitne', 'X ka hisaab'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "distributor_name": {
                        "type": "string",
                        "description": "Distributor name, partial or misspelled is fine (optional - omit for all).",
                    },
                    "category": {
                        "type": "string",
                        "description": "Bed, Bath, Pillow, TOB ... (optional).",
                    },
                    "season": {
                        "type": "string",
                        "description": "Season code, e.g. AW26 (optional).",
                    },
                },
            },
        },
        {
            "name": "search_nexora",
            "description": (
                "Search everything in NEXORA by a word or name - distributors, retailers, "
                "orders, stock, Article Master (MRP/PTR/ex-mill), visit logs. Use when no other "
                "tool fits, to find who/what the user means, or to look up any party, article "
                "or order detail."
            ),
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Word, name or number to search."}},
                "required": ["text"],
            },
        },
        {
            "name": "find_so_by_article",
            "description": (
                "Find the Sales Order number(s) for an article (brand / print name like "
                "Aster, Cardinal, BIAB, optionally with size) for a distributor, from the "
                "matched filled orders - with SO qty and value per SO. Use for 'X ke Aster "
                "ka SO number kya hai', 'kis SO mein Y hai', 'SO no. of article Z'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "article": {"type": "string", "description": "Brand / print / article name, e.g. Aster."},
                    "distributor_name": {"type": "string", "description": "Distributor (optional)."},
                    "category": {"type": "string", "description": "Bed, Bath, Pillow, TOB ... (optional)."},
                    "season": {"type": "string", "description": "Season code, e.g. AW26 (optional)."},
                },
                "required": ["article"],
            },
        },
        {
            "name": "get_pending_so",
            "description": (
                "Sales Orders not yet billed, or only partly billed, by a CI - SO "
                "number, distributor, season, category, SO qty/value, CI-billed "
                "qty/value and pending qty/value. Use for 'kitne SO pending hai', "
                "'kiska maal abhi bill nahi hua', 'pending SO kitna hai'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "distributor_name": {
                        "type": "string",
                        "description": "Filter to one distributor (optional).",
                    },
                    "category": {
                        "type": "string",
                        "description": "Bed, Bath, Pillow, TOB ... (optional).",
                    },
                    "season": {
                        "type": "string",
                        "description": "Season code, e.g. AW26 (optional).",
                    },
                },
            },
        },
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
        {
            "name": "get_party_profile",
            "description": (
                "Look up a distributor or retailer by name (typo-tolerant) - "
                "contact person, phone, address, GST, email, and their last visit "
                "date. Use for 'X ka number/address/GST/last visit kya hai' type "
                "questions. Tries distributors first, then retailers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "party_name": {
                        "type": "string",
                        "description": "The distributor or retailer's name (can be partial/misspelled).",
                    }
                },
                "required": ["party_name"],
            },
        },
        {
            "name": "get_article_price",
            "description": (
                "Search Article Master for MRP/PTR/ex-mill price by brand, product, "
                "or print style (e.g. 'Cardinal', 'towel'). Use for 'X ka MRP/rate/"
                "ex-mill kya hai' type questions. Optionally narrow to one exact size."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Brand, product, or print-style text to search for.",
                    },
                    "size": {
                        "type": "string",
                        "description": "Exact size string to narrow to, e.g. '(224 X 254)' (optional).",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "get_market_visits",
            "description": (
                "This user's logged DSR/Market Visit reports - retailer/customer "
                "name, location, order value, feedback/remarks. Filter by an exact "
                "date, a date range, and/or a customer name. Use for 'aaj kitne "
                "visit kiye', 'X ko kab visit kiya tha' type questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Exact visit date, YYYY-MM-DD (optional).",
                    },
                    "from_date": {
                        "type": "string",
                        "description": "Range start, YYYY-MM-DD (optional, ignored if date is set).",
                    },
                    "to_date": {
                        "type": "string",
                        "description": "Range end, YYYY-MM-DD (optional, ignored if date is set).",
                    },
                    "customer_name": {
                        "type": "string",
                        "description": "Filter to visits whose customer/retailer name contains this text (optional).",
                    },
                },
            },
        },
        {
            "name": "get_todo_list",
            "description": (
                "This user's personal To-Do task list - title, category, priority, "
                "status, due date. Defaults to open tasks (not done) if status is "
                "omitted. Use for 'aaj ke kaam kya hai', 'pending todo kya hai' "
                "type questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "One of pending, done, hold (optional - defaults to all not-done tasks).",
                    }
                },
            },
        },
        {
            "name": "get_grievance_status",
            "description": (
                "Distributor complaints/grievances this user logged - the problem, "
                "date, status (open/resolved), and solution text if resolved. "
                "Filter by distributor name and/or status. Use for 'X ki complaint "
                "ka kya hua', 'kitni grievance open hai' type questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "distributor_name": {
                        "type": "string",
                        "description": "Filter to one distributor (optional).",
                    },
                    "status": {
                        "type": "string",
                        "description": "'open' or 'resolved' (optional).",
                    },
                },
            },
        },
        {
            "name": "get_distributor_zone_summary",
            "description": (
                "One distributor's combined Distributor Zone snapshot - this "
                "fiscal year's target vs achievement, plus their most recent "
                "secondary-sale months (from DSR visit logs). Use for 'X ka "
                "distributor zone dikhao', 'X ka secondary sale kaisa hai' type "
                "questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "distributor_name": {
                        "type": "string",
                        "description": "The distributor's name (can be partial/misspelled).",
                    }
                },
                "required": ["distributor_name"],
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


# --- Conversation memory (follow-up questions) ----------------------------
# Each user's recent Ask Nexora turns, so "aur Bath ke?" or "uska payment?"
# stays about the distributor/item asked a moment ago. A turn older than
# _TURN_WINDOW_MIN minutes starts a fresh conversation.
_TURN_WINDOW_MIN = 20
_TURN_LIMIT = 6
_FOLLOW_UP_WORDS = (
    "uska", "uske", "uski", "usko", "usme", "usmein", "iska", "iske", "iski", "isme", "unka",
    "unke", "unki", "inka", "inke", "aur", "or", "bhi", "also", "same", "wahi", "vahi", "ye",
    "yeh", "wo", "woh", "voh", "its", "their", "that", "those", "them", "kitne", "kitna",
    "kitni", "kab", "kyun", "kaise", "details", "detail", "list",
)


def _ensure_turns_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ask_nexora_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            workspace_id TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ask_nexora_turns_user ON ask_nexora_turns (user_id, workspace_id, id)"
    )


def record_turn(user_id: int | None, workspace_id: str, question: str, answer: str) -> None:
    """Remember one question/answer; never breaks the answer itself."""
    if user_id is None or not (question or "").strip():
        return
    try:
        conn = sqlite3.connect(_db_path())
        try:
            _ensure_turns_schema(conn)
            conn.execute(
                "INSERT INTO ask_nexora_turns (user_id, workspace_id, question, answer) VALUES (?, ?, ?, ?)",
                (int(user_id), workspace_id or "default", question.strip()[:1000], (answer or "").strip()[:4000]),
            )
            # Keep the table small: only the last 50 turns per user matter.
            conn.execute(
                "DELETE FROM ask_nexora_turns WHERE user_id = ? AND workspace_id = ? AND id NOT IN "
                "(SELECT id FROM ask_nexora_turns WHERE user_id = ? AND workspace_id = ? ORDER BY id DESC LIMIT 50)",
                (int(user_id), workspace_id or "default", int(user_id), workspace_id or "default"),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def recent_turns(user_id: int | None, workspace_id: str) -> list[dict[str, str]]:
    """This user's turns from the current conversation, oldest first."""
    if user_id is None:
        return []
    try:
        conn = sqlite3.connect(_db_path())
        try:
            _ensure_turns_schema(conn)
            rows = conn.execute(
                "SELECT question, answer FROM ask_nexora_turns WHERE user_id = ? AND workspace_id = ? "
                "AND created_at >= datetime('now', ?) ORDER BY id DESC LIMIT ?",
                (int(user_id), workspace_id or "default", f"-{_TURN_WINDOW_MIN} minutes", _TURN_LIMIT),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    return [{"question": q, "answer": a} for q, a in reversed(rows)]


def is_follow_up(query: str, turns: list[dict[str, str]]) -> bool:
    """Does this question lean on the conversation so far?"""
    if not turns:
        return False
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in (query or "").lower()).split() if w]
    if not words:
        return False
    return len(words) <= 5 or any(w in _FOLLOW_UP_WORDS for w in words)


def _history_contents(turns: list[dict[str, str]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in turns or []:
        out.append({"role": "user", "parts": [{"text": t["question"]}]})
        out.append({"role": "model", "parts": [{"text": (t.get("answer") or "")[:1500] or "(no answer)"}]})
    return out


def _norm_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _name_filter(names: list[str], wanted: str) -> set[str] | None:
    """Names (as given) that match `wanted`; None means no filter asked.

    Substring on letters/digits first ("savitri" → "Savitri Steel Cement
    Traders"), then a close-spelling match for typos ("savitri steal")."""
    want = _norm_key(wanted)
    if not want:
        return None
    hits = {n for n in names if want in _norm_key(n) or (_norm_key(n) and _norm_key(n) in want)}
    if hits or len(want) < 5:
        # Too short to guess a misspelling safely ("zeta" is not "beta").
        return hits
    import difflib

    keys = {_norm_key(n): n for n in names if _norm_key(n)}
    close = difflib.get_close_matches(want, list(keys), n=3, cutoff=0.6)
    if not close:
        # Typo in a long name: compare against same-length prefixes.
        prefixes = {k[: len(want)]: k for k in keys}
        close = [prefixes[p] for p in difflib.get_close_matches(want, list(prefixes), n=3, cutoff=0.75)]
    return {keys[k] for k in close}


def _same(value: Any, wanted: str) -> bool:
    return not wanted or _norm_key(value) == _norm_key(wanted)


def hisaab_for_ai(
    ledger: dict[str, Any], *, distributor_name: str = "", category: str = "", season: str = ""
) -> dict[str, Any]:
    """Trim the Distributor Hisaab (build_ledger output) to what an answer needs."""
    dists = ledger.get("distributors") or []
    allowed = _name_filter([str(d.get("distributor_name") or "") for d in dists], distributor_name)
    if allowed is not None and not allowed:
        return {"error": f"No distributor matching '{distributor_name}' in Hisaab",
                "distributors_on_file": [d.get("distributor_name") for d in dists][:40]}
    out = []
    for d in dists:
        if allowed is not None and str(d.get("distributor_name") or "") not in allowed:
            continue
        cats = []
        for c in d.get("categories") or []:
            if not _same(c.get("category"), category) or not _same(c.get("season"), season):
                continue
            cis = c.get("cis") or []
            by_status: dict[str, int] = {}
            for ci in cis:
                by_status[ci.get("status") or "unpaid"] = by_status.get(ci.get("status") or "unpaid", 0) + 1
            pend = c.get("pending_sos") or []
            cats.append({
                "season": c.get("season"),
                "category": c.get("category"),
                "ci_count": len(cis),
                "ci_count_by_status": by_status,
                "ci_qty_total": round(sum(float(ci.get("qty") or 0) for ci in cis), 2),
                "ci_gross_total": round(sum(float(ci.get("gross") or 0) for ci in cis), 2),
                "cis": [
                    {k: ci.get(k) for k in ("invoice_no", "invoice_date", "so_number", "qty",
                                            "gross", "net", "covered", "status")}
                    for ci in cis[:60]
                ],
                "order_payable": c.get("order_payable"),
                "received": c.get("received"),
                "billed_net": c.get("billed"),
                "still_to_collect": c.get("payment_due"),
                "received_but_ci_not_made": c.get("unbilled_advance"),
                "due_date": c.get("due_date"),
                "cd_percent": c.get("cd_percent"),
                "tds_percent": c.get("tds_percent"),
                "pending_so_count": len(pend),
                "pending_so_qty": round(sum(float(p.get("pending_qty") or 0) for p in pend), 2),
                "pending_so_value": round(sum(float(p.get("pending_net") or 0) for p in pend), 2),
                "pending_sos": pend[:30],
            })
        if not cats and (category or season):
            continue
        out.append({
            "distributor": d.get("distributor_name"),
            "totals": d.get("totals"),
            "categories": cats,
            "cis_not_linked_to_matched_so": d.get("unlinked_cis") or [],
            "alerts": [a.get("text") for a in d.get("alerts") or []],
        })
    return {"as_of": ledger.get("as_of"), "distributors": out, "count": len(out)}


def so_by_article_for_ai(
    runs: list[dict[str, Any]], *, article: str, distributor_name: str = "", category: str = "", season: str = ""
) -> dict[str, Any]:
    """Which SO(s) carry an article (brand/size) - from the latest FO↔SO match runs.

    `runs` are full match runs (with rows), newest first; only the newest run per
    distributor/season/category counts, like the Order Desk."""
    want = _norm_key(article)
    if not want:
        return {"error": "article is required (brand / print name, e.g. Aster)"}
    allowed = _name_filter(sorted({str(r.get("distributor_name") or "") for r in runs}), distributor_name)
    if allowed is not None and not allowed:
        return {"error": f"No matched SOs for a distributor matching '{distributor_name}'", "matches": [], "count": 0}
    seen_slots: set[tuple[str, str, str]] = set()
    hits = []
    for run in runs:
        slot = (str(run.get("distributor_name") or ""), str(run.get("season") or ""), str(run.get("category") or ""))
        if slot in seen_slots:
            continue
        seen_slots.add(slot)
        if allowed is not None and slot[0] not in allowed:
            continue
        if not _same(slot[1], season) or not _same(slot[2], category):
            continue
        for row in run.get("rows") or []:
            brand = str(row.get("brand") or "")
            if want not in _norm_key(brand) and want not in _norm_key(row.get("match_key_brand")):
                continue
            hits.append({
                "distributor": slot[0], "season": slot[1], "category": slot[2],
                "brand": brand, "size": row.get("size"), "status": row.get("status"),
                "fo_qty": row.get("fo_qty"), "so_qty": row.get("so_qty"),
                "so_numbers": row.get("so_numbers") or [],
                "so_breakdown": [
                    {k: b.get(k) for k in ("so_number", "qty", "net")} for b in (row.get("so_breakdown") or [])
                ],
            })
    so_set = sorted({s for h in hits for s in h["so_numbers"]})
    return {"count": len(hits), "so_numbers": so_set, "matches": hits[:60]}


def pending_so_for_ai(
    items: list[dict[str, Any]], *, distributor_name: str = "", category: str = "", season: str = ""
) -> dict[str, Any]:
    """Pending SO rows filtered for one question, with per-category totals."""
    allowed = _name_filter(sorted({str(i.get("distributor_name") or "") for i in items}), distributor_name)
    if allowed is not None and not allowed:
        return {"error": f"No pending SO for a distributor matching '{distributor_name}'", "items": [], "count": 0}
    rows = [
        i for i in items
        if (allowed is None or str(i.get("distributor_name") or "") in allowed)
        and _same(i.get("category"), category) and _same(i.get("season"), season)
    ]
    summary: dict[str, dict[str, Any]] = {}
    for i in rows:
        key = f"{i.get('distributor_name') or '-'} | {i.get('season') or '-'} | {i.get('category') or '-'}"
        s = summary.setdefault(key, {"so_count": 0, "pending_qty": 0.0, "pending_value": 0.0})
        s["so_count"] += 1
        s["pending_qty"] = round(s["pending_qty"] + float(i.get("pending_qty") or 0), 2)
        s["pending_value"] = round(s["pending_value"] + float(i.get("pending_net") or 0), 2)
    slim = [
        {k: i.get(k) for k in ("so_number", "distributor_name", "season", "category", "status",
                               "so_qty", "so_net", "ci_qty", "ci_amount", "pending_qty", "pending_net")}
        for i in rows[:80]
    ]
    return {"count": len(rows), "by_distributor_season_category": summary, "items": slim}


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
        if name == "get_distributor_hisaab":
            from centralized_db_system.db import CentralizedDB
            from app.services.distributor_ledger import build_ledger

            ledger = build_ledger(
                CentralizedDB(_db_path()), conn, user_id=user_id, workspace_id=workspace_id
            )
            return _sanitize(hisaab_for_ai(
                ledger,
                distributor_name=str(args.get("distributor_name") or ""),
                category=str(args.get("category") or ""),
                season=str(args.get("season") or ""),
            ))

        if name == "propose_action":
            from app.services import nexora_actions

            params = args.get("params")
            out = nexora_actions.propose(
                _db_path(), user_id=user_id, workspace_id=workspace_id,
                action=str(args.get("action") or ""),
                params=params if isinstance(params, dict) else {},
                summary=str(args.get("summary") or ""),
            )
            if "action_id" in out:
                try:
                    from flask import g, has_request_context

                    if has_request_context():
                        g.nexora_pending_action = {"id": out["action_id"], "summary": out["summary"]}
                except Exception:
                    pass
            return out

        if name == "read_app":
            from app.services.nexora_app_reader import read_app

            query = args.get("query")
            return _sanitize(read_app(
                str(args.get("path") or ""),
                query if isinstance(query, dict) else None,
                user_id=user_id,
                workspace_id=workspace_id,
            ))

        if name == "search_nexora":
            from centralized_db_system.db import CentralizedDB

            text = str(args.get("text") or "").strip()
            if not text:
                return {"error": "text is required"}
            found = CentralizedDB(_db_path()).global_search(text, workspace_id=workspace_id, user_id=user_id)
            results = found.get("results") or {}
            return _sanitize({k: (v or [])[:10] for k, v in results.items() if v})

        if name == "find_so_by_article":
            runs = []
            for r in sorted(matchdb.list_match_runs(conn, user_id=user_id), key=lambda x: -int(x.get("id") or 0)):
                full = matchdb.get_match_run(conn, int(r["id"]), user_id=user_id)
                if full:
                    runs.append(full)
            return _sanitize(so_by_article_for_ai(
                runs,
                article=str(args.get("article") or ""),
                distributor_name=str(args.get("distributor_name") or ""),
                category=str(args.get("category") or ""),
                season=str(args.get("season") or ""),
            ))

        if name == "get_pending_so":
            items = matchdb.list_pending_so_for_user(conn, user_id)
            return _sanitize(pending_so_for_ai(
                items,
                distributor_name=str(args.get("distributor_name") or ""),
                category=str(args.get("category") or ""),
                season=str(args.get("season") or ""),
            ))

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

        if name == "get_party_profile":
            party_name = str(args.get("party_name") or "").strip()
            if not party_name:
                return {"error": "party_name is required"}

            from centralized_db_system.db import CentralizedDB
            from app.routes.data import _find_distributor_fuzzy

            db = CentralizedDB(_db_path())

            dist = _find_distributor_fuzzy(db, party_name, workspace_id)
            if dist:
                dist_id = dist.get("id")
                last_visit = db.get_last_visit_date("distributor", dist_id) if dist_id else None
                return _sanitize({
                    "type": "distributor",
                    "name": dist.get("firm_name") or dist.get("name"),
                    "nick_name": dist.get("firm_nick_name"),
                    "contact_person": dist.get("name"),
                    "phone": dist.get("phone_number"),
                    "address": dist.get("address"),
                    "location": dist.get("location"),
                    "gst_no": dist.get("gst_no"),
                    "email": dist.get("email"),
                    "credit_limit": dist.get("credit_limit"),
                    "payment_terms": dist.get("payment_terms"),
                    "status": dist.get("status"),
                    "last_visit": last_visit,
                })

            # No distributor matched — try retailers with the same
            # substring-on-name/owner/contact pattern the "identity" intent
            # already uses.
            like_query = f"%{party_name.lower()}%"
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, name, owner_name, contact_person, phone_number, "
                "address, location, gst_no, email FROM master_retailers "
                "WHERE workspace_id = ? AND (LOWER(name) LIKE ? OR "
                "LOWER(owner_name) LIKE ? OR LOWER(contact_person) LIKE ?) LIMIT 1",
                (workspace_id, like_query, like_query, like_query),
            ).fetchone()
            conn.row_factory = None
            if not row:
                return {"error": f"No distributor or retailer matching '{party_name}' found"}
            ret = dict(row)
            last_visit = db.get_last_visit_date("retailer", ret["id"]) if ret.get("id") else None
            return _sanitize({
                "type": "retailer",
                "name": ret.get("name"),
                "contact_person": ret.get("owner_name") or ret.get("contact_person"),
                "phone": ret.get("phone_number"),
                "address": ret.get("address"),
                "location": ret.get("location"),
                "gst_no": ret.get("gst_no"),
                "email": ret.get("email"),
                "last_visit": last_visit,
            })

        if name == "get_article_price":
            query = str(args.get("query") or "").strip()
            if not query:
                return {"error": "query is required"}
            size = str(args.get("size") or "").strip()
            conn.row_factory = sqlite3.Row
            sql = (
                "SELECT brand, size, bs_size, product, print_style, colors, mrp, "
                "selling_price, ptr, retailer_margin, exmill_price "
                "FROM article_master_v2 WHERE workspace_id = ? "
                "AND (brand LIKE ? OR product LIKE ? OR print_style LIKE ?)"
            )
            like = f"%{query}%"
            params: list[Any] = [workspace_id, like, like, like]
            if size:
                sql += " AND size = ?"
                params.append(size)
            sql += " ORDER BY brand, size LIMIT 25"
            rows = conn.execute(sql, params).fetchall()
            conn.row_factory = None
            articles = [dict(r) for r in rows]
            return {"articles": _sanitize(articles), "count": len(articles)}

        if name == "get_market_visits":
            from app.routes.dsr_market import _ensure_table as _ensure_market_table

            _ensure_market_table(conn)
            conn.row_factory = sqlite3.Row
            date_str = str(args.get("date") or "").strip()
            from_date = str(args.get("from_date") or "").strip()
            to_date = str(args.get("to_date") or "").strip()
            customer_name = str(args.get("customer_name") or "").strip()
            sql = (
                "SELECT visit_date, customer_name, location, owner_name, "
                "contact_nos, channel_type, order_lacs, retailer_feedback, "
                "sm_remarks FROM dsr_market_visits "
                "WHERE workspace_id = ? AND (user_id = ? OR user_id IS NULL)"
            )
            params: list[Any] = [workspace_id, user_id]
            if date_str:
                sql += " AND visit_date = ?"
                params.append(date_str)
            else:
                if from_date:
                    sql += " AND visit_date >= ?"
                    params.append(from_date)
                if to_date:
                    sql += " AND visit_date <= ?"
                    params.append(to_date)
            if customer_name:
                sql += " AND LOWER(customer_name) LIKE ?"
                params.append(f"%{customer_name.lower()}%")
            sql += " ORDER BY visit_date DESC, id DESC LIMIT 30"
            rows = conn.execute(sql, params).fetchall()
            conn.row_factory = None
            visits = [dict(r) for r in rows]
            return {"visits": _sanitize(visits), "count": len(visits)}

        if name == "get_todo_list":
            from app.routes.personal_todos import _ensure_table as _ensure_todo_table

            _ensure_todo_table(conn)
            conn.row_factory = sqlite3.Row
            status = str(args.get("status") or "").strip().lower()
            sql = (
                "SELECT task_title, category, person_party, given_by, priority, "
                "status, due_date, due_time FROM personal_todos "
                "WHERE workspace_id = ? AND user_id = ?"
            )
            params = [workspace_id, user_id]
            if status:
                sql += " AND status = ?"
                params.append(status)
            else:
                sql += " AND status != 'done'"
            sql += " ORDER BY due_date IS NULL, due_date ASC LIMIT 30"
            rows = conn.execute(sql, params).fetchall()
            conn.row_factory = None
            todos = [dict(r) for r in rows]
            return {"todos": _sanitize(todos), "count": len(todos)}

        if name == "get_grievance_status":
            from app.routes.distributor_grievances import _ensure_grievances_table

            _ensure_grievances_table(conn)
            conn.row_factory = sqlite3.Row
            distributor_name = str(args.get("distributor_name") or "").strip()
            status = str(args.get("status") or "").strip().lower()
            sql = (
                "SELECT distributor_name, problem_text, problem_date, status, "
                "solution_text, created_at, closed_at FROM distributor_grievances "
                "WHERE workspace_id = ? AND user_id = ?"
            )
            params = [workspace_id, user_id]
            if distributor_name:
                sql += " AND LOWER(distributor_name) LIKE ?"
                params.append(f"%{distributor_name.lower()}%")
            if status:
                sql += " AND status = ?"
                params.append(status)
            sql += " ORDER BY created_at DESC LIMIT 30"
            rows = conn.execute(sql, params).fetchall()
            conn.row_factory = None
            grievances = [dict(r) for r in rows]
            return {"grievances": _sanitize(grievances), "count": len(grievances)}

        if name == "get_distributor_zone_summary":
            distributor_name = str(args.get("distributor_name") or "").strip()
            if not distributor_name:
                return {"error": "distributor_name is required"}

            from centralized_db_system.db import CentralizedDB
            from app.routes.data import _find_distributor_fuzzy
            from app.routes.distributor_zone import (
                _breakup_index,
                _load_dsr_secondary_and_feedback,
                _match_breakup,
                _money_from_lakhs,
                _month_rows,
                _norm_name as _dz_norm_name,
                _pick_year,
            )

            db = CentralizedDB(_db_path())
            dist = _find_distributor_fuzzy(db, distributor_name, workspace_id)
            if not dist:
                return {"error": f"No distributor matching '{distributor_name}' found"}

            year = _pick_year(db, workspace_id, None)
            fy_label = (year or {}).get("fy_label") or ""
            resolved_year_id = (year or {}).get("year_id")

            target_money = None
            achieved_money = None
            if resolved_year_id:
                breakup = db.list_target_distributor_breakup(workspace_id, int(resolved_year_id))
                by_code, by_name = _breakup_index(breakup)
                matched = _match_breakup(dist, by_code, by_name)
                if matched:
                    target_lakhs = float(matched.get("target_lakhs") or 0)
                    ach_lakhs = float(matched.get("achievement_lakhs") or 0)
                    if ach_lakhs <= 0:
                        ach_lakhs = (
                            float(matched.get("achievement_excel") or 0)
                            + float(matched.get("achievement_ci") or 0)
                            + float(matched.get("achievement_manual") or 0)
                        )
                    target_money = _money_from_lakhs(target_lakhs)
                    achieved_money = _money_from_lakhs(ach_lakhs)

            months_by_id, _fb_by_id, months_by_name, _fb_by_name = (
                _load_dsr_secondary_and_feedback(workspace_id, None, None)
            )
            dist_id = dist.get("id")
            try:
                dist_id_int = int(dist_id) if dist_id is not None else None
            except (TypeError, ValueError):
                dist_id_int = None
            month_map: dict[str, float] = {}
            if dist_id_int and dist_id_int in months_by_id:
                month_map = dict(months_by_id[dist_id_int])
            else:
                n = _dz_norm_name(dist.get("firm_name") or dist.get("name"))
                if n and n in months_by_name:
                    month_map = dict(months_by_name[n])
            secondary_months = _month_rows(month_map)[:6]

            return _sanitize({
                "distributor": dist.get("firm_name") or dist.get("name"),
                "fiscal_year": fy_label,
                "target": target_money,
                "achieved": achieved_money,
                "recent_secondary_sale_months": secondary_months,
            })

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
    last_err = ""
    models = agent_gemini_models()
    for idx, model in enumerate(models):
        # A stuck model should hand over to the next one quickly; only the
        # last model gets the full wait (phone gives up after 120 s).
        timeout_s = _AGENT_TIMEOUT_S if idx == len(models) - 1 else _AGENT_FIRST_TRY_TIMEOUT_S
        model_body = body
        if model.startswith("gemini-3"):
            # Gemini 3 thinks by default and took 15-40 s per round on the server,
            # past the old 30 s timeout. Low thinking is enough to pick tools.
            model_body = {**body, "generationConfig": {
                **body["generationConfig"], "thinkingConfig": {"thinkingLevel": "low"}}}
        payload = json.dumps(model_body).encode("utf-8")
        url = _GEMINI_URL.format(model=model, key=key)
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8")), model
        except urllib.error.HTTPError as exc:
            try:
                last_err = exc.read().decode("utf-8", errors="ignore")[:300]
            except Exception:
                last_err = str(exc)
            if exc.code in (404, 429, 500, 503):
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


_DAILY_AI_LIMIT_DEFAULT = 50
_DAILY_VOICE_LIMIT_DEFAULT = 100


def daily_limit(kind: str) -> int:
    """Per-user AI calls allowed per day (IST). Env NEXORA_AI_DAILY_LIMIT /
    NEXORA_VOICE_DAILY_LIMIT override; 0 = no limit."""
    env = "NEXORA_VOICE_DAILY_LIMIT" if kind == "voice" else "NEXORA_AI_DAILY_LIMIT"
    default = _DAILY_VOICE_LIMIT_DEFAULT if kind == "voice" else _DAILY_AI_LIMIT_DEFAULT
    try:
        return max(0, int(os.getenv(env) or default))
    except ValueError:
        return default


def used_today(user_id: int, kind: str) -> int:
    """This user's AI calls today (India day), from ai_agent_usage_log."""
    feature_sql = "feature = 'nexora_voice'" if kind == "voice" else "feature LIKE 'nexora_agent%'"
    try:
        conn = sqlite3.connect(_db_path())
        try:
            _ensure_usage_schema(conn)
            row = conn.execute(
                f"SELECT count(*) FROM ai_agent_usage_log WHERE user_id = ? AND {feature_sql} "
                "AND date(created_at, '+330 minutes') = date('now', '+330 minutes')",
                (int(user_id),),
            ).fetchone()
        finally:
            conn.close()
        return int(row[0] or 0)
    except Exception:
        return 0


def over_daily_limit(user_id: int | None, kind: str) -> bool:
    limit = daily_limit(kind)
    return bool(user_id is not None and limit and used_today(int(user_id), kind) >= limit)


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


# --- Learned question patterns ---------------------------------------------
# When the agent answers a question with tools, the question's shape and the
# tools it needed are remembered ("{distributor_name} ke kitne ci pending hai"
# -> get_distributor_hisaab + get_pending_so). The next question with the same
# shape - for any distributor - fetches that data straight away and the AI
# only writes the answer: one round instead of two. Numbers are always fresh.
_LEARN_ARG_KEYS = ("distributor_name", "party_name", "customer_name", "category", "season", "query")


def _norm_text(text: str) -> str:
    return " ".join("".join(ch if ch.isalnum() else " " for ch in (text or "").lower()).split())


def _ensure_learned_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ask_nexora_learned (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            template TEXT NOT NULL,
            tools_json TEXT NOT NULL,
            learned_from TEXT,
            hits INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_used_at TEXT,
            UNIQUE (workspace_id, template)
        )
        """
    )


def learn_template(query: str, calls: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]] | None:
    """Question + tool calls -> (template, tools with {placeholders}), or None.

    Only values that literally appear in the question become placeholders, so
    the template can be filled from a new question of the same shape."""
    template = _norm_text(query)
    if not template or not calls:
        return None
    if any(str(c.get("name") or "") == "propose_action" for c in calls):
        return None  # changes are never replayed from a pattern
    tools = []
    for call in calls:
        name = str(call.get("name") or "")
        if not name:
            return None
        args_out: dict[str, Any] = {}
        for k, v in (call.get("args") or {}).items():
            val = _norm_text(str(v))
            if k in _LEARN_ARG_KEYS and val:
                if "{" + k + "}" in template:
                    args_out[k] = "{" + k + "}"
                    continue
                padded = f" {template} "
                if f" {val} " in padded:
                    template = padded.replace(f" {val} ", " {" + k + "} ", 1).strip()
                    args_out[k] = "{" + k + "}"
                    continue
            args_out[k] = v
        tools.append({"name": name, "args": args_out})
    if "{" not in template:
        return None  # nothing to fill in - a fixed question, no gain over asking again
    if len([w for w in template.split() if not w.startswith("{")]) < 2:
        return None  # too vague to recognise safely
    return template, tools


def _template_regex(template: str):
    import re

    parts = re.split(r"(\{[a-z_]+\})", template)
    seen: set[str] = set()
    out = []
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            key = part[1:-1]
            out.append(f"(?P<{key}>.+?)" if key not in seen else f"(?P={key})")
            seen.add(key)
        else:
            out.append(re.escape(part))
    return re.compile("^" + "".join(out) + "$")


def match_learned(query: str, rows: list[tuple[str, str]]) -> list[dict[str, Any]] | None:
    """Fill the first matching template's tools from `query`. rows = (template, tools_json)."""
    q = _norm_text(query)
    for template, tools_json in rows:
        m = _template_regex(template).match(q)
        if not m:
            continue
        values = m.groupdict()
        filled = []
        for tool in json.loads(tools_json):
            args = {}
            for k, v in (tool.get("args") or {}).items():
                if isinstance(v, str) and v.startswith("{") and v.endswith("}"):
                    v = values.get(v[1:-1], "")
                args[k] = v
            filled.append({"name": tool["name"], "args": args})
        return filled
    return None


def _learned_rows(workspace_id: str) -> list[tuple[str, str]]:
    try:
        conn = sqlite3.connect(_db_path())
        try:
            _ensure_learned_schema(conn)
            return conn.execute(
                "SELECT template, tools_json FROM ask_nexora_learned WHERE workspace_id = ? "
                "ORDER BY hits DESC, id DESC LIMIT 300",
                (workspace_id,),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []


def _save_learned(workspace_id: str, query: str, calls: list[dict[str, Any]]) -> None:
    learned = learn_template(query, calls)
    if not learned:
        return
    template, tools = learned
    try:
        conn = sqlite3.connect(_db_path())
        try:
            _ensure_learned_schema(conn)
            conn.execute(
                "INSERT INTO ask_nexora_learned (workspace_id, template, tools_json, learned_from) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(workspace_id, template) DO UPDATE SET "
                "tools_json = excluded.tools_json",
                (workspace_id, template, json.dumps(tools, ensure_ascii=False), query[:300]),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _mark_learned_used(workspace_id: str, query: str) -> None:
    q = _norm_text(query)
    try:
        conn = sqlite3.connect(_db_path())
        try:
            for (template,) in conn.execute(
                "SELECT template FROM ask_nexora_learned WHERE workspace_id = ?", (workspace_id,)
            ).fetchall():
                if _template_regex(template).match(q):
                    conn.execute(
                        "UPDATE ask_nexora_learned SET hits = hits + 1, last_used_at = datetime('now') "
                        "WHERE workspace_id = ? AND template = ?",
                        (workspace_id, template),
                    )
                    conn.commit()
                    break
        finally:
            conn.close()
    except Exception:
        pass


def ask_order_desk(
    query: str,
    *,
    user_id: int,
    workspace_id: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Run the tool-calling loop for one question (Order Desk, Target vs
    Achievement, PJP, or Distributor Payment Status) and return the final
    text answer. `history` = earlier turns of this conversation (follow-ups).
    Raises NexoraAiAgentError on hard failure."""
    if over_daily_limit(user_id, "ai"):
        # Callers fall back to the rule engine, so the user still gets an answer.
        raise NexoraAiAgentError(f"Daily AI limit ({daily_limit('ai')}) reached for this user")
    contents: list[dict[str, Any]] = [
        *_history_contents(history),
        {"role": "user", "parts": [{"text": query}]},
    ]
    rounds = 0
    prompt_tokens = 0
    output_tokens = 0
    model_used = ""
    first_round_calls: list[dict[str, Any]] = []
    used_learned = False

    # A question shaped like one answered before: fetch its data first, so the
    # AI only has to write the answer. Not for follow-ups (they lean on history).
    if not history:
        prefetch = match_learned(query, _learned_rows(workspace_id))
        if prefetch:
            results = [
                {"tool": c["name"], "args": c["args"],
                 "result": _run_tool(c["name"], c["args"], user_id=user_id, workspace_id=workspace_id)}
                for c in prefetch
            ]
            if not any(isinstance(r["result"], dict) and r["result"].get("error") for r in results):
                used_learned = True
                contents = [{"role": "user", "parts": [{"text": (
                    f"{query}\n\n[NEXORA already fetched this data for the question above. "
                    f"Answer from it; call a tool only if something needed is missing.]\n"
                    + json.dumps(_sanitize(results), ensure_ascii=False, default=str)[:60000]
                )}]}]

    def _finish(answer: str) -> str:
        _log_usage(
            user_id=user_id,
            feature="nexora_agent_learned" if used_learned else "nexora_agent",
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
            answer = "\n".join(text_parts).strip()
            if answer and used_learned:
                _mark_learned_used(workspace_id, query)
            elif answer and first_round_calls and not history and rounds == 2:
                # Plain one-step tool answer - worth remembering for next time.
                _save_learned(workspace_id, query, first_round_calls)
            return _finish(answer or "Iska jawab abhi nahi mil paaya.")

        if rounds == 1:
            first_round_calls = [
                {"name": c.get("name") or "", "args": c.get("args") or {}} for c in function_calls
            ]

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
        # Gemini 3 rejects role "function"; tool results go back as a user turn.
        contents.append({"role": "user", "parts": response_parts})

    return _finish(
        "Iska pakka jawab abhi nahi mil paaya. Kripya distributor, article, "
        "season ya category ka naam saath mein bataiye."
    )

"""Polish DSR retailer feedback + SM remarks via Gemini (free-tier API key).

Preserves manual free-text; weaves checklist facts into short pure-English prose.
Never invents facts. Does not change Excel column layout — only cell text.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from app.services.gemini_models import get_ocr_gemini_models


def gemini_api_key() -> str:
    for name in ("GEMINI_API_KEY", "GOOGLE_GEMINI_API_KEY"):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            val = (current_app.config.get("GEMINI_API_KEY") or "").strip()
            if val:
                return val
    except Exception:
        pass
    return ""


def gemini_configured() -> bool:
    return bool(gemini_api_key())


def _parse_intel(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    # Some models wrap JSON in prose — take first object span.
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(cleaned[start : end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    # Loose key extraction if JSON is slightly broken.
    fb = re.search(
        r'"retailer_feedback"\s*:\s*"(.*?)"\s*(?:,|\})',
        cleaned,
        flags=re.DOTALL,
    )
    sm = re.search(
        r'"sm_remarks"\s*:\s*"(.*?)"\s*(?:,|\})',
        cleaned,
        flags=re.DOTALL,
    )
    if fb or sm:
        def _unescape(s: str) -> str:
            return (
                s.replace("\\n", "\n")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
                .strip()
            )

        return {
            "retailer_feedback": _unescape(fb.group(1)) if fb else "",
            "sm_remarks": _unescape(sm.group(1)) if sm else "",
        }
    # Prose fallback: two labeled paragraphs.
    fb2 = re.search(
        r"(?:retailer[_\s-]*feedback|feedback)\s*[:\-]\s*(.+?)(?:\n\s*\n|sm[_\s-]*remarks|remarks\s*from|$)",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    sm2 = re.search(
        r"(?:sm[_\s-]*remarks|remarks(?:\s*from\s*sm)?)\s*[:\-]\s*(.+)$",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fb2 or sm2:
        return {
            "retailer_feedback": (fb2.group(1).strip() if fb2 else ""),
            "sm_remarks": (sm2.group(1).strip() if sm2 else ""),
        }
    return None


def _fallback_from_row(row: dict[str, Any]) -> dict[str, str]:
    return {
        "retailer_feedback": (row.get("retailer_feedback") or "").strip(),
        "sm_remarks": (row.get("sm_remarks") or "").strip(),
    }


def polish_visit_notes(row: dict[str, Any]) -> tuple[dict[str, str], str | None]:
    """Return ({retailer_feedback, sm_remarks}, error_code_or_None)."""
    key = gemini_api_key()
    if not key:
        return _fallback_from_row(row), "missing_api_key"

    intel = _parse_intel(row.get("visit_intel_json"))
    manual_issue = (intel.get("issue_detail") or "").strip()
    manual_expects = (intel.get("expects_from_bd") or "").strip()
    manual_next = (intel.get("next_action") or "").strip()
    sells_well = (intel.get("bd_sells_well") or "").strip()
    not_moving = (intel.get("bd_not_moving") or "").strip()

    payload = {
        "customer_name": row.get("customer_name"),
        "existing_feedback_cell": row.get("retailer_feedback"),
        "existing_remarks_cell": row.get("sm_remarks"),
        "manual_issue_detail": manual_issue,
        "manual_expects_from_bd": manual_expects,
        "manual_next_action": manual_next,
        "sells_well": sells_well,
        "not_moving": not_moving,
        "issues": intel.get("issues") or [],
        "retailer_response": intel.get("retailer_response"),
        "opportunity_score": intel.get("opportunity_score") or row.get("customer_type"),
        "visit_focus": intel.get("visit_focus"),
        "distributor": intel.get("distributor"),
        "brands_on_shelf": intel.get("brands_on_shelf") or [],
        "top_selling_brands": intel.get("top_selling_brands") or [],
        "bd_availability": intel.get("bd_availability"),
        "pending_since": intel.get("pending_since"),
        "follow_up_date": intel.get("follow_up_date"),
        "responsible": intel.get("responsible"),
    }

    prompt = f"""You write two short cells for a company Daily Sales Report (DSR) Excel.
This is ONE retailer / outlet visit — not a market overview.

Return ONLY valid JSON:
{{
  "retailer_feedback": "...",
  "sm_remarks": "..."
}}

Rules:
- Pure English only.
- Keep manual free-text almost as written (light grammar only). Manual fields are the hero.
- Do NOT invent facts, amounts, brands, grades, or dates.
- Do NOT write "overall market", area essays, or city market summaries.
- Do NOT use labels like "Issues:", "Visit focus:", or pipe "|" separators.
- retailer_feedback: 2–4 short sentences about THIS outlet only.
- sm_remarks: 2–3 short sentences; lead with manual_next_action when present.
- Max ~70 words per field. If a field has no facts, return empty string for that field.

Visit data JSON:
{json.dumps(payload, ensure_ascii=False)}
"""

    body_json = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 400,
            "responseMimeType": "application/json",
        },
    }
    body = json.dumps(body_json).encode("utf-8")
    body_plain = json.dumps(
        {
            "contents": body_json["contents"],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 400,
            },
        }
    ).encode("utf-8")

    models = get_ocr_gemini_models()
    # Prefer lighter flash models for text polish (free-tier friendly).
    preferred = (
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-flash-latest",
        "gemini-2.0-flash-lite-001",
        "gemini-1.5-flash",
        "gemini-1.5-flash-8b",
    )
    ordered: list[str] = []
    for m in preferred + tuple(models):
        if m not in ordered:
            ordered.append(m)

    last_error = "api_error"
    last_detail = ""
    saw_quota = False
    for model in ordered:
        for payload in (body, body_plain):
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={key}"
            )
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=45) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                cands = data.get("candidates") or []
                if not cands:
                    last_error = "no_candidates"
                    continue
                parts = (cands[0].get("content") or {}).get("parts") or []
                text = ""
                for part in parts:
                    if isinstance(part, dict) and part.get("text"):
                        text += part["text"]
                parsed = _extract_json_object(text)
                if not parsed:
                    last_error = "unparsed"
                    last_detail = (text or "")[:180].replace("\n", " ")
                    continue
                feedback = str(parsed.get("retailer_feedback") or "").strip()
                remarks = str(parsed.get("sm_remarks") or "").strip()
                if not feedback and not remarks:
                    last_error = "empty"
                    continue
                # Safety: if model blanked a field that had content, keep original.
                fallback = _fallback_from_row(row)
                return {
                    "retailer_feedback": feedback or fallback["retailer_feedback"],
                    "sm_remarks": remarks or fallback["sm_remarks"],
                }, None
            except urllib.error.HTTPError as exc:
                try:
                    detail = json.loads(exc.read().decode("utf-8"))
                    msg = (detail.get("error", {}) or {}).get("message", "") or ""
                except Exception:
                    msg = ""
                last_detail = (msg or str(exc))[:220]
                if exc.code == 429 or "quota" in msg.lower():
                    saw_quota = True
                    last_error = "quota_exceeded"
                    # Try next model — free tier quotas are often per-model.
                    break
                if exc.code in (401, 403) or "API key" in msg:
                    return _fallback_from_row(row), "invalid_api_key"
                if exc.code == 404:
                    last_error = "model_not_found"
                    break
                last_error = f"http_{exc.code}"
                continue
            except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError, IndexError):
                last_error = "api_error"
                continue
            except Exception:
                last_error = "api_error"
                continue

    if saw_quota and last_error.startswith("quota_exceeded"):
        detail_bit = f" ({last_detail})" if last_detail else ""
        return _fallback_from_row(row), f"quota_exceeded{detail_bit}"
    if last_detail and last_error in {"unparsed", "empty", "no_candidates"}:
        return _fallback_from_row(row), f"{last_error}: {last_detail}"
    if last_detail and last_error.startswith("http_"):
        return _fallback_from_row(row), f"{last_error}:{last_detail}"
    return _fallback_from_row(row), last_error


def polish_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Polish feedback/remarks in-place copies. Returns (rows, meta)."""
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    polished = 0
    for row in rows:
        copy = dict(row)
        notes, err = polish_visit_notes(copy)
        if err:
            errors.append(err)
        else:
            polished += 1
            copy["retailer_feedback"] = notes["retailer_feedback"]
            copy["sm_remarks"] = notes["sm_remarks"]
        out.append(copy)
    meta = {
        "gemini_configured": gemini_configured(),
        "polished_count": polished,
        "row_count": len(rows),
        "errors": sorted(set(errors)),
    }
    return out, meta

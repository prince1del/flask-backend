"""Deterministic retailer-visit narrative for HO Excel fields.

Generates `retailer_feedback` and `sm_remarks` from structured questionnaire /
visit_intel_json. Same seed always yields the same wording. Never invents facts.
Narrative is a human-readable projection only — never parse it back into data.
"""

from __future__ import annotations

import json
import re
from typing import Any

DEFAULT_BRAND = "Bombay Dyeing"

# ---------------------------------------------------------------------------
# Template banks (block-level variation; not full report templates)
# ---------------------------------------------------------------------------

INTRO_TEMPLATES = [
    "During the visit to {retailer}, the interaction covered current business and growth potential.",
    "The visit to {retailer} focused on reviewing outlet performance and opportunities.",
    "At {retailer}, the discussion centred on present assortment and business outlook.",
    "The engagement with {retailer} reviewed current trading and placement scope.",
    "While meeting {retailer}, the conversation covered shelf performance and next steps.",
    "The call at {retailer} assessed the outlet's current business and potential.",
    "A review visit was conducted at {retailer} covering performance and opportunities.",
    "The retailer interaction at {retailer} covered business status and growth areas.",
]

ISSUE_TEMPLATES = [
    "The retailer highlighted concerns regarding {issues}.",
    "Key concerns raised included {issues}.",
    "Issues flagged during the visit were {issues}.",
    "The outlet reported challenges around {issues}.",
    "Concerns were noted on {issues}.",
    "The retailer pointed out difficulties with {issues}.",
    "Discussion covered pending concerns related to {issues}.",
    "The visit surfaced issues around {issues}.",
]

SELLING_TEMPLATES = [
    "{items} are currently showing good movement at the outlet.",
    "Categories performing well include {items}.",
    "Strong movement was noted in {items}.",
    "{items} continue to sell well at this outlet.",
    "The retailer indicated healthy sales in {items}.",
    "Good traction was observed for {items}.",
    "Currently, {items} are moving well on the shelf.",
    "Outlet feedback suggests {items} are selling steadily.",
]

PLACEMENT_TEMPLATES = [
    "Additional placement potential was identified in {items}.",
    "There is scope to expand placement in {items}.",
    "Placement opportunity was noted for {items}.",
    "Business potential exists in placing {items}.",
    "Further assortment opportunity was seen in {items}.",
    "The outlet shows placement potential for {items}.",
    "Room for incremental placement was identified in {items}.",
    "Expansion opportunity was discussed for {items}.",
]

MANUAL_TRANSITION_TEMPLATES = [
    "The retailer additionally mentioned that {text}",
    "The retailer specifically mentioned that {text}",
    "In their own words, the retailer shared that {text}",
    "The retailer further noted that {text}",
    "Manual feedback from the retailer: {text}",
    "The retailer also highlighted that {text}",
    "Additional retailer feedback was that {text}",
    "The retailer emphasised that {text}",
]

EXPECTS_TEMPLATES = [
    "{text} are expected from {brand}.",
    "He has requested {text} from {brand}.",
    "The retailer expects {text} from {brand}.",
    "Support sought from {brand} includes {text}.",
    "Expectations from {brand}: {text}.",
    "The outlet has asked for {text} from {brand}.",
]

AREA_NOTE_TEMPLATES = [
    "Area observation: {text}",
    "Market note: {text}",
    "Local market context noted: {text}",
    "Area/market feedback: {text}",
]

VISIT_FOCUS_TEMPLATES = [
    "The interaction focused on {focus}.",
    "The visit centred on {focus}.",
    "Focus of the call was {focus}.",
    "The engagement was oriented around {focus}.",
    "This visit addressed {focus}.",
]

RESPONSE_TEMPLATES = {
    "follow-up required": [
        "Further follow-up is required to progress the opportunity.",
        "The retailer requires follow-up to move the opportunity forward.",
        "Continued follow-up is needed before the opportunity can advance.",
        "A follow-up is required to take this opportunity further.",
        "Progress depends on timely follow-up with the retailer.",
        "The response indicates that follow-up is still required.",
    ],
    "ready to order": [
        "The retailer is ready to place an order.",
        "The outlet indicated readiness to order.",
        "Order readiness was confirmed during the visit.",
        "The retailer is prepared to proceed with an order.",
        "The response confirms the retailer is ready to order.",
        "Commercial discussion can proceed as the retailer is ready to order.",
    ],
    "not interested": [
        "The retailer is not interested at this stage.",
        "Interest was not confirmed during this visit.",
        "The outlet is currently not interested in proceeding.",
        "The retailer declined interest for now.",
        "No commercial interest was expressed at this time.",
        "The response indicates the retailer is not interested presently.",
    ],
    "default": [
        "The retailer's response was noted as {response}.",
        "Retailer response captured: {response}.",
        "The outlet response was recorded as {response}.",
        "Response from the retailer: {response}.",
        "The visit recorded the retailer response as {response}.",
    ],
}

OPPORTUNITY_TEMPLATES = {
    "A": [
        "The outlet has been assessed as a Category A opportunity.",
        "Opportunity grading is Category A.",
        "This account is classified as Category A.",
        "The retailer is rated Category A on opportunity.",
    ],
    "B": [
        "The outlet has been assessed as a Category B opportunity.",
        "Opportunity grading is Category B.",
        "This account is classified as Category B.",
        "The retailer is rated Category B on opportunity.",
    ],
    "C": [
        "The outlet has been assessed as a Category C opportunity.",
        "Opportunity grading is Category C.",
        "This account is classified as Category C.",
        "The retailer is rated Category C on opportunity.",
    ],
    "D": [
        "The outlet has been assessed as a Category D opportunity.",
        "Opportunity grading is Category D.",
        "This account is classified as Category D.",
        "The retailer is rated Category D on opportunity.",
    ],
}

DISTRIBUTOR_TEMPLATES = [
    "The outlet is currently serviced by {distributor}.",
    "Distribution is handled by {distributor}.",
    "The retailer is serviced through {distributor}.",
    "Current distributor on record is {distributor}.",
    "Supply is routed via {distributor}.",
]

NEXT_ACTION_TEMPLATES = [
    "The next action is to {action}.",
    "Next step: {action}.",
    "Agreed next action is to {action}.",
    "Follow-up action planned: {action}.",
    "The SM will next {action}.",
    "Immediate next action is {action}.",
]


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


def _stable_hash(seed: str) -> int:
    """Deterministic non-crypto hash (same across Python runs)."""
    h = 0
    for ch in seed:
        h = (h * 31 + ord(ch)) & 0x7FFFFFFF
    return h


def choose_template(templates: list[str], retailer_id: str, visit_date: str, block_name: str) -> str:
    if not templates:
        return ""
    seed = f"{retailer_id or ''}_{visit_date or ''}_{block_name}"
    return templates[_stable_hash(seed) % len(templates)]


def join_naturally(items: list[str]) -> str:
    cleaned = [str(x).strip() for x in items if str(x).strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return f"{', '.join(cleaned[:-1])} and {cleaned[-1]}"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            s = str(item).strip()
            if s:
                out.append(s)
        return out
    text = str(value).strip()
    if not text:
        return []
    # Free-text multi values often comma / slash / pipe separated.
    parts = re.split(r"[,;/|]+", text)
    cleaned = [p.strip() for p in parts if p.strip()]
    return cleaned if cleaned else [text]


def clean_manual_feedback(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", str(text).strip())
    if not cleaned:
        return ""
    cleaned = cleaned[0].upper() + cleaned[1:] if len(cleaned) > 1 else cleaned.upper()
    if cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def feedback_without_end_punctuation(text: str) -> str:
    cleaned = clean_manual_feedback(text)
    if cleaned.endswith((".", "!", "?")):
        return cleaned[:-1].rstrip()
    return cleaned


def _normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _issues_covered_by_manual(issues: list[str], issue_detail: str) -> bool:
    """True when manual feedback already restates the structured issues."""
    detail = _normalize_key(issue_detail)
    if not detail or not issues:
        return False
    hits = 0
    for issue in issues:
        key = _normalize_key(issue)
        if not key:
            continue
        tokens = [t for t in key.split() if len(t) > 3]
        if not tokens:
            if key in detail:
                hits += 1
            continue
        if any(t in detail for t in tokens):
            hits += 1
    return hits >= max(1, (len(issues) + 1) // 2)


def _response_bucket(response: str) -> str:
    key = _normalize_key(response)
    if "follow" in key and "up" in key:
        return "follow-up required"
    if "ready" in key and "order" in key:
        return "ready to order"
    if "not interest" in key or key in {"not interested", "no interest"}:
        return "not interested"
    return "default"


def _ensure_sentence(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if text[-1] not in ".!?":
        text += "."
    return text


def _lower_first_if_needed(text: str) -> str:
    """For embedding cleaned manual text after 'that '."""
    text = feedback_without_end_punctuation(text)
    if not text:
        return ""
    # Keep acronyms / proper nouns starting with multiple caps.
    if len(text) > 1 and text[0].isupper() and text[1].isupper():
        return text
    return text[0].lower() + text[1:] if text else text


def build_retailer_feedback(
    *,
    intel: dict[str, Any],
    retailer_name: str = "",
    retailer_id: str = "",
    visit_date: str = "",
    brand_name: str = DEFAULT_BRAND,
) -> str:
    brand = (brand_name or DEFAULT_BRAND).strip() or DEFAULT_BRAND
    rid = str(retailer_id or intel.get("retailer_id") or retailer_name or "")
    vdate = str(visit_date or "")[:10]
    name = (retailer_name or "").strip() or "the retailer"

    issues = _as_list(intel.get("issues"))
    sells = _as_list(intel.get("bd_sells_well"))
    placement = _as_list(intel.get("placement_categories"))
    issue_detail = (intel.get("issue_detail") or "").strip()
    expects = (intel.get("expects_from_bd") or "").strip()
    area_notes = (intel.get("area_market_notes") or "").strip()

    parts: list[str] = []

    # 1. Observation / intro — only when we have a concrete retailer name.
    if retailer_name.strip():
        intro = choose_template(INTRO_TEMPLATES, rid, vdate, "INTRO")
        parts.append(_ensure_sentence(intro.format(retailer=name)))

    # 2. Issues — SKIP entirely when empty; never invent "satisfied".
    if issues and not _issues_covered_by_manual(issues, issue_detail):
        issue_text = join_naturally([_chip_phrase(i) for i in issues])
        tmpl = choose_template(ISSUE_TEMPLATES, rid, vdate, "ISSUES")
        parts.append(_ensure_sentence(tmpl.format(issues=issue_text)))

    # 3. Selling well
    if sells:
        sell_text = join_naturally([_chip_phrase(s, title_case=True) for s in sells])
        tmpl = choose_template(SELLING_TEMPLATES, rid, vdate, "SELLING")
        parts.append(_ensure_sentence(tmpl.format(items=sell_text)))

    # 4. Placement
    if placement:
        place_text = join_naturally([_chip_phrase(p, title_case=True) for p in placement])
        # Soft de-dupe vs sells: if identical sets, still allow placement wording (different meaning).
        tmpl = choose_template(PLACEMENT_TEMPLATES, rid, vdate, "PLACEMENT")
        parts.append(_ensure_sentence(tmpl.format(items=place_text)))

    # 5. Manual issue_detail (hero voice) — separate sentence
    if issue_detail:
        body = _lower_first_if_needed(issue_detail)
        tmpl = choose_template(MANUAL_TRANSITION_TEMPLATES, rid, vdate, "MANUAL")
        # Templates that already end without period before {text}
        sentence = tmpl.format(text=body)
        parts.append(_ensure_sentence(sentence))

    # 6. expects_from_bd — separate sentence
    if expects:
        expect_body = feedback_without_end_punctuation(expects)
        # Prefer mid-sentence lower for "are expected..." templates when starting with capital.
        expect_mid = expect_body[0].lower() + expect_body[1:] if expect_body else expect_body
        tmpl = choose_template(EXPECTS_TEMPLATES, rid, vdate, "EXPECTS")
        if "{text}" in tmpl and tmpl.strip().startswith("{text}"):
            parts.append(_ensure_sentence(tmpl.format(text=expect_body, brand=brand)))
        else:
            parts.append(_ensure_sentence(tmpl.format(text=expect_mid, brand=brand)))

    # 7. Area / market notes (only if entered)
    if area_notes:
        note = clean_manual_feedback(area_notes)
        note_body = note[:-1] if note.endswith(".") else note
        tmpl = choose_template(AREA_NOTE_TEMPLATES, rid, vdate, "AREA")
        parts.append(_ensure_sentence(tmpl.format(text=note_body)))

    return " ".join(p for p in parts if p).strip()


def _chip_phrase(text: str, title_case: bool = False) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if title_case:
        # Preserve multi-word chips; capitalize words lightly.
        return " ".join(w[:1].upper() + w[1:] if w else w for w in t.split())
    # Mid-sentence: lower first character unless acronym.
    if len(t) > 1 and t[0].isupper() and t[1].islower():
        return t[0].lower() + t[1:]
    return t


def build_sm_remarks(
    *,
    intel: dict[str, Any],
    retailer_id: str = "",
    visit_date: str = "",
    customer_type: str | None = None,
) -> str:
    rid = str(retailer_id or intel.get("retailer_id") or "")
    vdate = str(visit_date or "")[:10]

    focus = (intel.get("visit_focus") or "").strip()
    response = (intel.get("retailer_response") or intel.get("response") or "").strip()
    opportunity = (
        (intel.get("opportunity_score") or intel.get("opportunity") or customer_type or "")
        .strip()
        .upper()
    )
    distributor = (intel.get("distributor") or "").strip()
    next_action = (intel.get("next_action") or "").strip()

    parts: list[str] = []

    if focus:
        focus_phrase = _chip_phrase(focus)
        tmpl = choose_template(VISIT_FOCUS_TEMPLATES, rid, vdate, "FOCUS")
        parts.append(_ensure_sentence(tmpl.format(focus=focus_phrase)))

    if response:
        bucket = _response_bucket(response)
        bank = RESPONSE_TEMPLATES.get(bucket) or RESPONSE_TEMPLATES["default"]
        tmpl = choose_template(bank, rid, vdate, "RESPONSE")
        if "{response}" in tmpl:
            parts.append(_ensure_sentence(tmpl.format(response=response)))
        else:
            parts.append(_ensure_sentence(tmpl))

    if opportunity in OPPORTUNITY_TEMPLATES:
        tmpl = choose_template(OPPORTUNITY_TEMPLATES[opportunity], rid, vdate, "OPPORTUNITY")
        parts.append(_ensure_sentence(tmpl))

    if distributor:
        tmpl = choose_template(DISTRIBUTOR_TEMPLATES, rid, vdate, "DISTRIBUTOR")
        parts.append(_ensure_sentence(tmpl.format(distributor=distributor)))

    if next_action:
        action = feedback_without_end_punctuation(next_action)
        action_mid = action[0].lower() + action[1:] if action else action
        # Avoid "to to " if user already starts with "to "
        if action_mid.lower().startswith("to "):
            action_mid = action_mid[3:].lstrip()
        tmpl = choose_template(NEXT_ACTION_TEMPLATES, rid, vdate, "NEXT_ACTION")
        if "to {action}" in tmpl:
            parts.append(_ensure_sentence(tmpl.format(action=action_mid)))
        else:
            parts.append(_ensure_sentence(tmpl.format(action=action_mid)))

    return " ".join(p for p in parts if p).strip()


def generate_visit_narratives(
    *,
    visit_intel_json: Any = None,
    retailer_name: str = "",
    retailer_id: Any = None,
    visit_date: str = "",
    customer_type: str | None = None,
    brand_name: str = DEFAULT_BRAND,
) -> dict[str, str]:
    """Build both HO narrative fields from structured intel."""
    intel = _parse_intel(visit_intel_json)
    rid = ""
    if retailer_id not in (None, ""):
        rid = str(retailer_id)
    elif intel.get("retailer_id") not in (None, ""):
        rid = str(intel.get("retailer_id"))

    feedback = build_retailer_feedback(
        intel=intel,
        retailer_name=retailer_name or "",
        retailer_id=rid,
        visit_date=visit_date or "",
        brand_name=brand_name,
    )
    remarks = build_sm_remarks(
        intel=intel,
        retailer_id=rid,
        visit_date=visit_date or "",
        customer_type=customer_type,
    )
    return {
        "retailer_feedback": feedback,
        "sm_remarks": remarks,
    }

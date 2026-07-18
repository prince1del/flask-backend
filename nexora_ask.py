"""
NEXORA Ask — rule-based Q&A over filled orders (Hindi / English / Hinglish).

Numbers always come from SQLite; this module does not invent quantities.
Optional LLM polish can be added later via GEMINI_API_KEY.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from rapidfuzz import fuzz

import article_master_parser as amp
import filled_orders_db as fodb
import nexora_ask_learn as learn
from centralized_db_system.order_reconciliation import PRODUCT_LABELS, normalize_product_code

_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "what", "how", "much", "many",
    "did", "do", "does", "order", "ordered", "orders", "qty", "quantity", "pieces",
    "pcs", "piece", "bales", "bale", "total", "amount", "value", "ex", "mill",
    "exmill", "ex-mill", "for", "in", "of", "and", "or", "tell", "me", "about",
    "show", "give", "please", "kitna", "kitni", "kitne", "kya", "ka", "ke", "ki", "ko",
    "ne", "mein", "main", "men", "se", "par", "pe", "kaun", "kiya", "kiye", "hai",
    "thi", "the", "hua", "hui", "hain", "product", "item", "line", "distributor",
    "dist", "agency", "agencies", "international", "ltd", "pvt", "private", "limited",
    "corner", "dyeing", "bombay", "textiles", "textile",
    "gst", "address", "phone", "mobile", "email", "pincode", "retailer", "retailers",
    "shop", "dukaan", "target", "achievement", "fiscal", "jankari", "janakari", "detail",
    "details", "number", "naam", "pata", "owner", "contact",
    "mrp", "ptr", "kon", "kaun", "kis", "kimat",
}

_SEASON_RE = re.compile(
    r"\b((?:AW|SS)\s*\d{2}(?:\s+[A-Za-z]+)?)\b",
    re.IGNORECASE,
)
_QTY_HINTS = ("qty", "quantity", "kitna", "kitni", "pieces", "pcs", "bales")
_TOTAL_HINTS = ("total", "amount", "value", "ex-mill", "exmill", "rupee", "rupaye", "₹", "kitna", "kitne", "maal", "order")
_AMOUNT_PHRASES = ("kitne ka", "kitna hai", "kitne hai", "kitne ka hai", "order value")
_EX_MILL_PHRASES = ("ex mill", "ex-mill", "exmill", "ex factory")
_ARTICLE_PRICE_HINTS = ("mrp", "ptr", "kimat", "retail price", "price")
_IDENTITY_PHRASES = (
    "kon hai", "kaun hai", "who is", "who are", "kis hai", "kya hai",
    "kaun sa", "kis distributor", "which distributor",
)
_NICK_HINTS = (
    "nick", "nickname", "nick name", "nick-name", "short name", "shortname",
    "code name", "codename", "alias",
)
_BRAND_EX_MILL_RE = re.compile(
    r"([a-z0-9][a-z0-9\s\-]*?)\s+ka\s+ex\s*-?\s*mill",
    re.IGNORECASE,
)
_BRAND_KI_PRICE_RE = re.compile(
    r"([a-z0-9][a-z0-9\s\-]*?)\s+ki\s+(?:mrp|ptr|kimat|price)",
    re.IGNORECASE,
)
_FY_RE = re.compile(r"(?:fy\s*)?(\d{2,4})\s*[-–/]\s*(\d{2,4})", re.IGNORECASE)
_PARTY_INFO_HINTS = (
    "gst", "address", "pata", "phone", "mobile", "email", "pincode", "location",
    "jankari", "janakari", "detail", "details", "full name", "buyer code", "zone", "region",
    "nick", "nickname", "nick name", "firm name", "firm naam",
)
_RETAILER_HINTS = ("retailer", "retailers", "dukaan", "shop", "store")
_TA_HINTS = ("target", "achievement", "achiev", "achivement", "ta ", " fiscal", "fy ")
_DIST_LABEL_FIELDS = ("firm_name", "firm_nick_name", "name", "distributor_code", "buyer_code")
_RETAIL_LABEL_FIELDS = ("name", "retailer_code", "owner_name", "contact_person")


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _detect_lang_hint(question: str) -> str:
    if re.search(r"[\u0900-\u097F]", question or ""):
        return "hi"
    hindi_words = ("kitna", "kitni", "kya", "ka", "ke", "ki", "ne", "mein", "kiya", "hai", "batao", "bata")
    lower = (question or "").lower()
    if any(w in lower for w in hindi_words):
        return "hi"
    return "en"


def _parse_season(question: str) -> str | None:
    match = _SEASON_RE.search(question or "")
    if not match:
        return None
    return _normalize_space(match.group(1).replace("  ", " "))


def _parse_scope_hints(question: str) -> dict[str, str | None]:
    """Infer category / season fragment from words like towel, bedsheet."""
    lower = (question or "").lower()
    if "towel" in lower:
        return {"category": "Bath", "season_contains": "towel"}
    if "bedsheet" in lower or "bed sheet" in lower or "bed linen" in lower:
        return {"category": "Bed", "season_contains": "bed"}
    if re.search(r"\bbed\b", lower) and "bedsheet" not in lower:
        return {"category": "Bed", "season_contains": None}
    if "bath" in lower:
        return {"category": "Bath", "season_contains": None}
    return {"category": None, "season_contains": None}


def _wants_amount_or_total(lower: str) -> bool:
    if any(phrase in lower for phrase in _AMOUNT_PHRASES):
        return True
    if any(p in lower for p in _EX_MILL_PHRASES) and ("order" in lower or "maal" in lower):
        return True
    return any(h in lower for h in _TOTAL_HINTS if h not in ("ex-mill", "exmill", "kitna", "kitne"))


def _wants_ex_mill_price_lookup(lower: str) -> bool:
    """Article Master unit price — not distributor order totals."""
    if not any(p in lower for p in _EX_MILL_PHRASES):
        return False
    if "order" in lower or "maal" in lower:
        return False
    return True


def _wants_article_price_lookup(lower: str) -> bool:
    if not any(h in lower for h in _ARTICLE_PRICE_HINTS):
        return False
    if "order" in lower or "maal" in lower:
        return False
    return True


def _wants_identity_info(lower: str) -> bool:
    # "Bernina ka GST kya hai" must NOT become a full identity dump
    if _requested_party_fields(lower):
        return False
    return any(p in lower for p in _IDENTITY_PHRASES)


_SIZE_LABEL_ALIASES: dict[str, str] = {
    **{v.lower(): k for k, v in PRODUCT_LABELS.items()},
    "king bedsheet": "KS BS",
    "king bed sheet": "KS BS",
    "king bed": "KS BS",
    "king bs": "KS BS",
    "double bedsheet": "DB BS",
    "double bed sheet": "DB BS",
    "single bedsheet": "SB BS",
    "single bed sheet": "SB BS",
}


def _normalize_size_hint(text: str) -> str | None:
    cleaned = _normalize_space(text)
    if not cleaned:
        return None
    lower = cleaned.lower()
    for label, code in sorted(_SIZE_LABEL_ALIASES.items(), key=lambda item: -len(item[0])):
        if lower == label or lower.endswith(f" {label}") or lower == label.replace(" ", ""):
            return code
    code = normalize_product_code(cleaned)
    return code if code in PRODUCT_LABELS else None


def _split_brand_and_size(phrase: str) -> tuple[str, str | None]:
    phrase = _normalize_space(phrase)
    if not phrase:
        return "", None
    lower = phrase.lower()

    for label, code in sorted(_SIZE_LABEL_ALIASES.items(), key=lambda item: -len(item[0])):
        if lower.endswith(label):
            brand = phrase[: len(phrase) - len(label)].strip()
            if brand:
                return brand, code
        compact_label = label.replace(" ", "")
        if lower.endswith(compact_label) and len(compact_label) >= 3:
            brand = phrase[: len(phrase) - len(compact_label)].strip()
            if brand:
                return brand, code

    tokens = phrase.split()
    for tail_len in (2, 1):
        if len(tokens) <= tail_len:
            continue
        tail = " ".join(tokens[-tail_len:])
        code = _normalize_size_hint(tail)
        if code:
            brand = " ".join(tokens[:-tail_len]).strip()
            if brand:
                return brand, code

    return phrase, None


def _parse_article_brand_size_hints(
    question: str,
    distributor: dict[str, Any] | None,
    distributors: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    raw = _extract_article_brand_hint(question, distributor, distributors)
    if not raw:
        raw = _extract_brand_hint(question, distributor, distributors)
    if not raw:
        return None, None
    brand, size = _split_brand_and_size(raw)
    if not brand or _looks_like_distributor_name(brand, distributors):
        return None, None
    return brand, size


def _size_label(size_code: str | None) -> str:
    code = normalize_product_code(size_code or "")
    return PRODUCT_LABELS.get(code, size_code or "")


def _sizes_match(size_hint: str | None, article_size: str | None) -> bool:
    if not size_hint:
        return True
    return normalize_product_code(size_hint) == normalize_product_code(article_size or "")


def _article_query_label(brand: str, size: str | None) -> str:
    code = normalize_product_code(size or "")
    friendly = _size_label(code)
    if code:
        return f"{brand} {code}" + (f" ({friendly})" if friendly and friendly != code else "")
    return brand


def _extract_article_brand_hint(
    question: str,
    distributor: dict[str, Any] | None,
    distributors: list[dict[str, Any]],
) -> str | None:
    lower = (question or "").lower()
    match = _BRAND_KI_PRICE_RE.search(lower)
    if match:
        hint = _normalize_space(match.group(1))
        if hint and hint not in _STOP_WORDS and not _looks_like_distributor_name(hint, distributors):
            return hint
    return _extract_brand_hint(question, distributor, distributors)


def _looks_like_distributor_name(hint: str, distributors: list[dict[str, Any]]) -> bool:
    hint_lower = hint.lower()
    for dist in distributors:
        for field in ("firm_name", "firm_nick_name", "name"):
            label = (dist.get(field) or "").strip()
            if not label:
                continue
            label_lower = label.lower()
            if hint_lower in label_lower or label_lower in hint_lower:
                return True
            if fuzz.partial_ratio(hint_lower, label_lower) >= 85:
                return True
    return False


def _extract_brand_hint(
    question: str,
    distributor: dict[str, Any] | None,
    distributors: list[dict[str, Any]],
) -> str | None:
    lower = (question or "").lower()
    match = _BRAND_EX_MILL_RE.search(lower)
    if match:
        hint = _normalize_space(match.group(1))
        if hint and hint not in _STOP_WORDS and not _looks_like_distributor_name(hint, distributors):
            return hint
    tokens = _product_tokens(question, distributor)
    if not tokens:
        return None
    joined = _normalize_space(" ".join(tokens))
    if _looks_like_distributor_name(joined, distributors):
        return None
    return joined


def _format_inr(value: float | None) -> str:
    num = float(value or 0)
    return f"{num:,.2f}"


def _format_qty(value: float | None) -> str:
    num = float(value or 0)
    if abs(num - round(num)) < 1e-9:
        return str(int(round(num)))
    return f"{num:,.2f}"


def _distributor_label(row: dict[str, Any]) -> str:
    return (row.get("firm_name") or row.get("name") or f"Distributor #{row.get('id')}").strip()


def _sort_chars_key(text: str) -> str:
    return "".join(sorted(re.findall(r"[a-z0-9]", (text or "").lower())))


def _question_tokens(question: str) -> list[str]:
    return [
        t for t in re.findall(r"[a-z0-9]+", (question or "").lower())
        if t not in _STOP_WORDS
    ]


def _nick_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _find_distributor_by_nickname(
    question: str, distributors: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Exact nickname match (bnd / SUP / PTJ / Choice Corner)."""
    tokens = set(_question_tokens(question))
    q_compact = _nick_key(question)
    best: dict[str, Any] | None = None
    best_len = 0
    for dist in distributors:
        nick = (dist.get("firm_nick_name") or "").strip()
        if not nick:
            continue
        nick_l = nick.lower()
        nick_compact = _nick_key(nick)
        nick_tokens = set(re.findall(r"[a-z0-9]+", nick_l))
        hit = False
        if nick_l in tokens or nick_compact in tokens:
            hit = True
        elif nick_tokens and nick_tokens.issubset(tokens):
            hit = True
        elif q_compact and q_compact == nick_compact:
            hit = True
        elif " " in nick_l and nick_l in (question or "").lower():
            hit = True
        if hit and len(nick_compact) >= best_len:
            best = dist
            best_len = len(nick_compact)
    return best


def _entity_match_score(query: str, label: str) -> int:
    """Fuzzy match tolerant to word order and minor spelling slips."""
    if not query or not label:
        return 0
    q = query.lower()
    l = label.lower()
    if l in q or q in l:
        q_tokens = set(re.findall(r"[a-z0-9]+", q))
        l_tokens = set(re.findall(r"[a-z0-9]+", l))
        if l_tokens and l_tokens.issubset(q_tokens):
            return 100
        if len(l) >= 4:
            return 100
        return 92
    scores = [
        fuzz.WRatio(q, l),
        fuzz.token_set_ratio(q, l),
        fuzz.partial_ratio(q, l),
    ]
    q_tokens = [
        t for t in re.findall(r"[a-z0-9]+", q)
        if t not in _STOP_WORDS and len(t) >= 3
    ]
    if q_tokens:
        scores.append(fuzz.token_set_ratio(" ".join(q_tokens), l))
        for tok in q_tokens:
            if tok in l:
                scores.append(96)
            scores.append(fuzz.partial_ratio(tok, l))
            if len(tok) >= 4:
                tok_sorted = _sort_chars_key(tok)
                for word in re.findall(r"[a-z0-9]+", l):
                    if len(word) >= 4 and fuzz.ratio(tok_sorted, _sort_chars_key(word)) >= 88:
                        scores.append(90)
    return max(scores) if scores else 0


def _find_best_entity(
    question: str,
    entities: list[dict[str, Any]],
    label_fields: tuple[str, ...],
    *,
    min_score: int = 70,
) -> dict[str, Any] | None:
    best: tuple[int, dict[str, Any] | None] = (0, None)
    for entity in entities:
        for field in label_fields:
            label = (entity.get(field) or "").strip()
            if not label:
                continue
            score = _entity_match_score(question, label)
            if score > best[0]:
                best = (score, entity)
    if best[0] >= min_score and best[1]:
        return best[1]
    return None


def _parse_fy(question: str) -> str | None:
    from app.fiscal_year import normalize_fiscal_year

    match = _FY_RE.search(question or "")
    if not match:
        return None
    raw = f"{match.group(1)}-{match.group(2)}"
    normalized = normalize_fiscal_year(raw)
    return normalized or raw


def _wants_nickname_info(lower: str) -> bool:
    if any(h in lower for h in _NICK_HINTS):
        return True
    if re.search(r"\b(kis|kaun|which)\s+(sa\s+)?(distributor|dist|agency)\b", lower):
        return True
    if re.search(r"\b(distributor|dist|agency)\s+(kaun|kis|which)\b", lower):
        return True
    return False


def _wants_party_profile(lower: str) -> bool:
    if any(h in lower for h in _PARTY_INFO_HINTS):
        return True
    if any(h in lower for h in _RETAILER_HINTS):
        return True
    if re.search(r"\b(ka|ki|ke)\s+(gst|address|phone|email|pata|detail|nick)", lower):
        return True
    return False


def _requested_party_fields(lower: str) -> set[str] | None:
    """Return specific master fields the user asked for, or None for full profile.

    Example: "bernina ka gst number aur address" → {gst_no, address}
    """
    text = (lower or "").lower()
    wants_full = any(
        h in text
        for h in (
            "jankari", "janakari", "detail", "details", "full profile",
            "poori", "puri info", "sab detail", "all detail", "complete",
        )
    )
    keys: set[str] = set()
    if "gst" in text:
        keys.add("gst_no")
    if any(h in text for h in ("address", "pata", "addres")):
        keys.add("address")
    if any(h in text for h in ("phone", "mobile", "contact number", "mobile number")):
        keys.add("phone_number")
    if "email" in text or "mail" in text:
        keys.add("email")
    if "pincode" in text or "pin code" in text or "pin-" in text:
        keys.add("pincode")
    if "location" in text or "city" in text or "jagah" in text:
        keys.add("location")
    if any(h in text for h in ("nick", "nickname", "short name", "shortname")):
        keys.add("firm_nick_name")
    if "firm name" in text or "firm naam" in text:
        keys.add("firm_name")
    if "buyer code" in text or "buyercode" in text:
        keys.add("buyer_code")
    if "distributor code" in text or "dist code" in text:
        keys.add("distributor_code")
    if "retailer code" in text:
        keys.add("retailer_code")
    if "zone" in text:
        keys.add("zone")
    if "region" in text:
        keys.add("region")
    if "payment" in text or "credit" in text:
        keys.update({"payment_terms", "credit_limit"})
    if "status" in text:
        keys.add("status")

    # Specific field ask wins over vague "details" in the same sentence
    if keys:
        return keys
    if wants_full:
        return None
    return None


def _wants_ta_info(lower: str) -> bool:
    return any(h in lower for h in _TA_HINTS) or _FY_RE.search(lower) is not None


def _is_retailer_question(lower: str) -> bool:
    return any(h in lower for h in _RETAILER_HINTS)


def _load_distributors(conn: sqlite3.Connection, workspace_id: str | None) -> list[dict[str, Any]]:
    # Isolation: never return cross-workspace masters
    if not workspace_id:
        return []
    query = """
        SELECT
            id, distributor_id, distributor_code, name, firm_name, firm_nick_name,
            phone_number, location, address, pincode, email, gst_no, buyer_code,
            zone, region, payment_terms, credit_limit, status
        FROM master_distributors
        WHERE workspace_id = ?
        ORDER BY id
    """
    params: list[Any] = [workspace_id]
    try:
        rows = conn.execute(query, params).fetchall()
    except sqlite3.OperationalError:
        # Older schemas may lack columns; missing table => empty (workspace isolation).
        try:
            rows = conn.execute(
                "SELECT id, name, firm_name, firm_nick_name FROM master_distributors"
                " WHERE workspace_id = ? ORDER BY id",
                params,
            ).fetchall()
            return [
                {"id": r[0], "name": r[1], "firm_name": r[2], "firm_nick_name": r[3]}
                for r in rows
            ]
        except sqlite3.OperationalError:
            return []
    cols = [
        "id", "distributor_id", "distributor_code", "name", "firm_name", "firm_nick_name",
        "phone_number", "location", "address", "pincode", "email", "gst_no", "buyer_code",
        "zone", "region", "payment_terms", "credit_limit", "status",
    ]
    return [dict(zip(cols, r)) for r in rows]


def _load_retailers(conn: sqlite3.Connection, workspace_id: str | None) -> list[dict[str, Any]]:
    # Isolation: never return cross-workspace masters
    if not workspace_id:
        return []
    query = """
        SELECT
            id, retailer_id, retailer_code, name, distributor_id, location,
            phone_number, email, address, gst_no, owner_name, contact_person,
            state, pincode, category, status
        FROM master_retailers
        WHERE workspace_id = ?
        ORDER BY id
    """
    params: list[Any] = [workspace_id]
    try:
        rows = conn.execute(query, params).fetchall()
    except sqlite3.OperationalError:
        return []
    keys = (
        "id", "retailer_id", "retailer_code", "name", "distributor_id", "location",
        "phone_number", "email", "address", "gst_no", "owner_name", "contact_person",
        "state", "pincode", "category", "status",
    )
    return [dict(zip(keys, row)) for row in rows]


def _find_distributor(question: str, distributors: list[dict[str, Any]]) -> dict[str, Any] | None:
    by_nick = _find_distributor_by_nickname(question, distributors)
    if by_nick:
        return by_nick
    found = _find_best_entity(question, distributors, _DIST_LABEL_FIELDS, min_score=70)
    if found:
        return found
    text = (question or "").lower()
    first = re.split(r"\s+ne\s+|\s+ka\s+|\s+ke\s+|\s+order", text, maxsplit=1)[0].strip(" ?,.")
    if first and len(first) >= 3:
        return _find_best_entity(first, distributors, _DIST_LABEL_FIELDS, min_score=78)
    return None


def _find_retailer(question: str, retailers: list[dict[str, Any]]) -> dict[str, Any] | None:
    return _find_best_entity(question, retailers, _RETAIL_LABEL_FIELDS, min_score=70)


def _find_person_entity(
    question: str,
    distributors: list[dict[str, Any]],
    retailers: list[dict[str, Any]],
) -> tuple[dict[str, Any], str] | None:
    """Match a person across distributor / retailer / sales-executive fields."""
    dist = _find_distributor(question, distributors)
    if dist:
        return dist, "distributor"
    retail = _find_retailer(question, retailers)
    if retail:
        return retail, "retailer"

    exec_fields = (
        ("sales_executive_name", "distributor"),
        ("secondary_distributor_name", "distributor"),
        ("owner_name", "retailer"),
        ("contact_person", "retailer"),
        ("secondary_retailer_name", "retailer"),
    )
    best: tuple[int, dict[str, Any] | None, str] = (0, None, "")
    for entity_list, default_type in ((distributors, "distributor"), (retailers, "retailer")):
        for entity in entity_list:
            for field, entity_type in exec_fields:
                label = (entity.get(field) or "").strip()
                if not label:
                    continue
                score = _entity_match_score(question, label)
                if score > best[0]:
                    best = (score, entity, entity_type)
    if best[0] >= 70 and best[1]:
        return best[1], best[2]
    return None


def _party_field_lines(
    entity: dict[str, Any],
    entity_type: str,
    lang: str,
    only_keys: set[str] | None = None,
) -> list[str]:
    if entity_type == "retailer":
        fields = [
            ("name", "Shop / Name", "Dukaan / Naam"),
            ("retailer_code", "Retailer code", "Retailer code"),
            ("owner_name", "Owner", "Malik"),
            ("contact_person", "Contact person", "Contact person"),
            ("gst_no", "GST No", "GST number"),
            ("address", "Address", "Pata"),
            ("location", "Location", "Location"),
            ("state", "State", "State"),
            ("pincode", "Pincode", "Pincode"),
            ("phone_number", "Phone", "Phone"),
            ("email", "Email", "Email"),
            ("category", "Category", "Category"),
            ("status", "Status", "Status"),
        ]
    else:
        fields = [
            ("firm_name", "Firm name", "Firm naam"),
            ("firm_nick_name", "Nickname", "Nick name"),
            ("name", "Name", "Naam"),
            ("distributor_code", "Distributor code", "Distributor code"),
            ("buyer_code", "Buyer code", "Buyer code"),
            ("gst_no", "GST No", "GST number"),
            ("address", "Address", "Pata"),
            ("location", "Location", "Location"),
            ("pincode", "Pincode", "Pincode"),
            ("zone", "Zone", "Zone"),
            ("region", "Region", "Region"),
            ("phone_number", "Phone", "Phone"),
            ("email", "Email", "Email"),
            ("payment_terms", "Payment terms", "Payment terms"),
            ("credit_limit", "Credit limit", "Credit limit"),
            ("status", "Status", "Status"),
        ]
    lines: list[str] = []
    for key, en_label, hi_label in fields:
        if only_keys is not None and key not in only_keys:
            continue
        val = entity.get(key)
        if val is None or str(val).strip() == "":
            if only_keys is not None and key in only_keys:
                label = hi_label if lang == "hi" else en_label
                missing = "available nahi" if lang == "hi" else "not on file"
                lines.append(f"• **{label}:** _{missing}_")
            continue
        label = hi_label if lang == "hi" else en_label
        lines.append(f"• **{label}:** {val}")
    return lines


def _format_party_profile(
    entity: dict[str, Any],
    entity_type: str,
    lang: str,
    only_keys: set[str] | None = None,
) -> str:
    title = (
        entity.get("firm_name") or entity.get("name") or f"{entity_type.title()} #{entity.get('id')}"
    )
    lines = _party_field_lines(entity, entity_type, lang, only_keys=only_keys)
    if not lines:
        return (
            f"**{title}** ke liye master record mein abhi detail nahi hai."
            if lang == "hi"
            else f"No profile details stored yet for **{title}**."
        )
    if only_keys is not None:
        # Short answer: party name + only asked fields
        return f"**{title}**\n" + "\n".join(lines)
    header = (
        f"**{title}** — {'retailer' if entity_type == 'retailer' else 'distributor'} details:"
        if lang == "en"
        else f"**{title}** — {'retailer' if entity_type == 'retailer' else 'distributor'} ki jankari:"
    )
    return header + "\n" + "\n".join(lines)


def _find_ta_year_id(
    conn: sqlite3.Connection,
    workspace_id: str | None,
    fy_label: str | None,
) -> tuple[int | None, str | None]:
    from app.fiscal_year import normalize_fiscal_year

    if not workspace_id:
        return None, None
    query = "SELECT id, financial_year, year FROM target_achievement_years WHERE workspace_id = ?"
    params: list[Any] = [workspace_id]
    try:
        rows = conn.execute(query, params).fetchall()
    except sqlite3.OperationalError:
        return None, None
    target = normalize_fiscal_year(fy_label) if fy_label else None
    for row in rows:
        year_id = int(row[0])
        label = normalize_fiscal_year(row[1] or row[2] or "") or (row[1] or row[2] or "")
        if not target:
            continue
        if label == target or target in str(row[1] or "") or target in str(row[2] or ""):
            return year_id, label
    return None, None


def _format_ta_summary(summary: dict[str, Any], fy_label: str, lang: str) -> str:
    target = float(summary.get("target_lakhs") or 0)
    ach = float(summary.get("active_achievement") or 0)
    pct = float(summary.get("percentage") or 0)
    if lang == "hi":
        return (
            f"**{fy_label}** Target vs Achievement (lakhs mein):\n"
            f"• Target: **{target:,.2f}**\n"
            f"• Achievement: **{ach:,.2f}**\n"
            f"• %: **{pct:,.2f}%**"
        )
    return (
        f"**{fy_label}** Target vs Achievement (lakhs):\n"
        f"• Target: **{target:,.2f}**\n"
        f"• Achievement: **{ach:,.2f}**\n"
        f"• %: **{pct:,.2f}%**"
    )


def _format_ta_overview(rows: list[dict[str, Any]], lang: str) -> str:
    if not rows:
        return (
            "Abhi koi fiscal year target set nahi hai."
            if lang == "hi"
            else "No fiscal year targets found yet."
        )
    lines = []
    for row in rows:
        fy = row.get("fy") or "—"
        target = float(row.get("target") or 0)
        ach = float(row.get("achievement") or 0)
        pct = float(row.get("percentage") or 0)
        lines.append(f"• **{fy}** — Target {target:,.2f}, Achievement {ach:,.2f} ({pct:,.2f}%)")
    header = "**Sab FY — Target vs Achievement (lakhs):**" if lang == "hi" else "**All FY — Target vs Achievement (lakhs):**"
    return header + "\n" + "\n".join(lines)


def _answer_ta_question(
    conn: sqlite3.Connection,
    workspace_id: str | None,
    db_path: str | None,
    distributor: dict[str, Any] | None,
    fy_label: str | None,
    lang: str,
) -> dict[str, Any] | None:
    if not db_path or not workspace_id:
        return None
    from centralized_db_system.db import CentralizedDB

    cdb = CentralizedDB(db_path)
    try:
        cdb.merge_duplicate_fiscal_years(workspace_id)
    except Exception:
        pass

    if distributor and fy_label:
        year_id, resolved_fy = _find_ta_year_id(conn, workspace_id, fy_label)
        if not year_id:
            return {
                "answer": (
                    f"FY **{fy_label}** master mein nahi mila."
                    if lang == "hi"
                    else f"FY **{fy_label}** not found."
                ),
                "intent": "ta_info",
                "data": {},
            }
        breakup = cdb.list_target_distributor_breakup(workspace_id, year_id)
        dist_name = _distributor_label(distributor)
        match_row = None
        best_score = 0
        for row in breakup:
            attr = (row.get("attribute_name") or row.get("distributor_name") or "").strip()
            if not attr:
                continue
            score = _entity_match_score(dist_name, attr)
            if score > best_score:
                best_score = score
                match_row = row
        if not match_row or best_score < 70:
            return {
                "answer": (
                    f"**{dist_name}** ke liye **{resolved_fy or fy_label}** mein TA breakup nahi mila."
                    if lang == "hi"
                    else f"No TA breakup for **{dist_name}** in **{resolved_fy or fy_label}**."
                ),
                "intent": "ta_info",
                "data": {"distributor_id": distributor.get("id"), "fy": resolved_fy},
            }
        target = float(match_row.get("target_amount") or 0)
        ach = float(match_row.get("achievement_amount") or 0)
        pct = float(match_row.get("achievement_percent") or 0)
        if lang == "hi":
            answer = (
                f"**{dist_name}** — **{resolved_fy or fy_label}** (lakhs):\n"
                f"• Target: **{target:,.2f}**\n"
                f"• Achievement: **{ach:,.2f}**\n"
                f"• %: **{pct:,.2f}%**"
            )
        else:
            answer = (
                f"**{dist_name}** — **{resolved_fy or fy_label}** (lakhs):\n"
                f"• Target: **{target:,.2f}**\n"
                f"• Achievement: **{ach:,.2f}**\n"
                f"• %: **{pct:,.2f}%**"
            )
        return {"answer": answer, "intent": "ta_info", "data": {"fy": resolved_fy}}

    if fy_label:
        year_id, resolved_fy = _find_ta_year_id(conn, workspace_id, fy_label)
        if not year_id:
            return {
                "answer": f"FY **{fy_label}** nahi mila." if lang == "hi" else f"FY **{fy_label}** not found.",
                "intent": "ta_info",
                "data": {},
            }
        summary = cdb.build_fy_achievement_summary(workspace_id, year_id, resolved_fy or fy_label)
        return {
            "answer": _format_ta_summary(summary, resolved_fy or fy_label, lang),
            "intent": "ta_info",
            "data": {"fy": resolved_fy},
        }

    overview_rows = []
    try:
        raw_rows = conn.execute(
            "SELECT id, financial_year, year FROM target_achievement_years WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    from app.fiscal_year import normalize_fiscal_year, fiscal_year_sort_key

    for row in raw_rows:
        year_id = int(row[0])
        fy = normalize_fiscal_year(row[1] or row[2] or "") or (row[1] or row[2] or "")
        try:
            summary = cdb.build_fy_achievement_summary(workspace_id, year_id, fy)
        except Exception:
            summary = {"target_lakhs": 0, "active_achievement": 0, "percentage": 0}
        overview_rows.append({
            "fy": fy,
            "target": summary.get("target_lakhs") or 0,
            "achievement": summary.get("active_achievement") or 0,
            "percentage": summary.get("percentage") or 0,
        })
    overview_rows.sort(key=lambda r: fiscal_year_sort_key(r.get("fy")))
    return {
        "answer": _format_ta_overview(overview_rows, lang),
        "intent": "ta_info",
        "data": {"fy_count": len(overview_rows)},
    }


def _product_tokens(question: str, distributor: dict[str, Any] | None) -> list[str]:
    text = (question or "").lower()
    for field in ("firm_name", "firm_nick_name", "name"):
        if distributor and distributor.get(field):
            text = text.replace(distributor[field].lower(), " ")
            for word in re.findall(r"[a-z0-9]+", distributor[field].lower()):
                if len(word) >= 4:
                    text = text.replace(word, " ")
    text = _SEASON_RE.sub(" ", text)
    scope = _parse_scope_hints(question)
    for scope_word in ("towel", "towels", "bedsheet", "bedsheets", "bath", "bed", "sheet"):
        text = text.replace(scope_word, " ")
    if scope.get("season_contains"):
        text = text.replace(scope["season_contains"], " ")
    tokens = re.findall(r"[a-z0-9]+", text)
    return [t for t in tokens if len(t) >= 2 and t not in _STOP_WORDS]


def _score_item(tokens: list[str], brand: str | None, size: str | None, product_type: str | None) -> int:
    joined = " ".join(tokens)
    if not joined:
        return 0
    if brand and amp.brands_match_fuzzy(joined, brand):
        return 100
    haystack = " ".join(
        part for part in ((brand or ""), (size or ""), (product_type or "")) if part
    ).lower()
    if not haystack:
        return 0
    score = max(
        fuzz.token_set_ratio(joined, haystack),
        fuzz.partial_ratio(joined, haystack),
    )
    if len(joined) <= 6 and score < 85:
        return 0
    return score


def _query_articles_by_brand(
    conn: sqlite3.Connection,
    user_id: int,
    brand_hint: str,
    workspace_id: str | None = None,
    size_hint: str | None = None,
) -> list[dict[str, Any]]:
    if not workspace_id:
        return []
    query = """
        SELECT category, product_type, brand, size, ex_mill_price, mrp, ptr, season_tag
        FROM article_master
        WHERE user_id = ? AND is_active = 1 AND workspace_id = ?
        ORDER BY brand, size
    """
    params: list[Any] = [user_id, workspace_id]
    rows = conn.execute(query, params).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        brand = row[2]
        if not brand or not amp.brands_match_fuzzy(brand_hint, brand):
            continue
        if size_hint and not _sizes_match(size_hint, row[3]):
            continue
        results.append({
            "category": row[0],
            "product_type": row[1],
            "brand": row[2],
            "size": row[3],
            "ex_mill_price": row[4],
            "mrp": row[5],
            "ptr": row[6],
            "season_tag": row[7],
        })
    return results


def _format_article_ex_mill_answer(
    articles: list[dict[str, Any]],
    brand_hint: str,
    lang: str,
    size_hint: str | None = None,
) -> str:
    query_label = _article_query_label(brand_hint, size_hint)
    if not articles:
        if lang == "hi":
            return (
                f"Article Master mein **{query_label}** nahi mila. "
                "Brand/size check karein — jaise *Florentine KS BS* (King Bedsheet)."
            )
        return (
            f"**{query_label}** not found in Article Master. "
            "Check brand and size (e.g. Florentine KS BS = King Bedsheet)."
        )
    lines: list[str] = []
    for article in articles[:12]:
        size_code = normalize_product_code(article.get("size") or "")
        size_bits = [article.get("size"), _size_label(size_code)]
        size_bits = [s for i, s in enumerate(size_bits) if s and s not in size_bits[:i]]
        product = " / ".join(
            p for p in (article.get("product_type"), *size_bits) if p
        ) or "—"
        category = article.get("category") or "—"
        ex_mill = float(article.get("ex_mill_price") or 0)
        if lang == "hi":
            lines.append(
                f"**{article.get('brand')}** — {category}, {product}: "
                f"Ex-mill **₹{_format_inr(ex_mill)}** per pc"
            )
        else:
            lines.append(
                f"**{article.get('brand')}** — {category}, {product}: "
                f"ex-mill **₹{_format_inr(ex_mill)}** per pc"
            )
    header = (
        f"**{query_label}** ka Article Master ex-mill:"
        if lang == "hi"
        else f"Article Master ex-mill for **{query_label}**:"
    )
    if len(articles) > 12:
        extra = len(articles) - 12
        lines.append(
            f"…aur {extra} aur size/variant." if lang == "hi" else f"…and {extra} more size(s)."
        )
    return header + "\n" + "\n".join(lines)


def _format_nickname_answer(entity: dict[str, Any], lang: str) -> str:
    """Answer nickname → firm mapping only — do not dump the whole master."""
    nick = (entity.get("firm_nick_name") or "").strip() or "—"
    firm = (entity.get("firm_name") or entity.get("name") or "—").strip()
    person = (entity.get("name") or "").strip()
    if lang == "hi":
        lines = [f"**{nick}** nick name hai **{firm}** distributor ka."]
        if person and person.lower() != firm.lower():
            lines.append(f"• **Contact / naam:** {person}")
    else:
        lines = [f"**{nick}** is the nickname for distributor **{firm}**."]
        if person and person.lower() != firm.lower():
            lines.append(f"• **Contact / name:** {person}")
    return "\n".join(lines)


def _format_identity_answer(
    entity: dict[str, Any],
    entity_type: str,
    lang: str,
) -> str:
    if entity_type == "distributor":
        person = entity.get("name") or entity.get("firm_name") or "—"
        firm = entity.get("firm_name") or entity.get("name") or "—"
        nick = (entity.get("firm_nick_name") or "").strip()
        if lang == "hi":
            intro = (
                f"**{person}** ek registered **distributor** hai"
                f"{f' — firm **{firm}**' if firm != person else ''}."
            )
            if nick:
                intro = (
                    f"**{nick}** nick name hai **{firm}** distributor ka"
                    f"{f' (contact: **{person}**)' if person and person != firm else ''}."
                )
        else:
            intro = (
                f"**{person}** is a registered **distributor**"
                f"{f' — firm **{firm}**' if firm != person else ''}."
            )
            if nick:
                intro = (
                    f"**{nick}** is the nickname for distributor **{firm}**"
                    f"{f' (contact: **{person}**)' if person and person != firm else ''}."
                )
    else:
        person = entity.get("name") or entity.get("owner_name") or "—"
        if lang == "hi":
            intro = f"**{person}** ek registered **retailer** hai."
        else:
            intro = f"**{person}** is a registered **retailer**."
    profile = _format_party_profile(entity, entity_type, lang, only_keys=None)
    return intro + "\n" + profile.split("\n", 1)[-1]


def _format_article_prices_answer(
    articles: list[dict[str, Any]],
    brand_hint: str,
    lang: str,
    size_hint: str | None = None,
) -> str:
    query_label = _article_query_label(brand_hint, size_hint)
    if not articles:
        if lang == "hi":
            return (
                f"Article Master mein **{query_label}** nahi mila. "
                "Brand/size check karein — *KS BS* = King Bedsheet, *DB BS* = Double Bedsheet."
            )
        return (
            f"**{query_label}** not found in Article Master. "
            "Check brand/size — KS BS = King Bedsheet, DB BS = Double Bedsheet."
        )
    lines: list[str] = []
    for article in articles[:12]:
        size_code = normalize_product_code(article.get("size") or "")
        size_bits = [article.get("size"), _size_label(size_code)]
        size_bits = [s for i, s in enumerate(size_bits) if s and s not in size_bits[:i]]
        product = " / ".join(
            p for p in (article.get("product_type"), *size_bits) if p
        ) or "—"
        category = article.get("category") or "—"
        mrp = float(article.get("mrp") or 0)
        ptr = float(article.get("ptr") or 0)
        ex_mill = float(article.get("ex_mill_price") or 0)
        if lang == "hi":
            lines.append(
                f"**{article.get('brand')}** — {category}, {product}: "
                f"MRP **₹{_format_inr(mrp)}**, PTR **₹{_format_inr(ptr)}**, "
                f"Ex-mill **₹{_format_inr(ex_mill)}**"
            )
        else:
            lines.append(
                f"**{article.get('brand')}** — {category}, {product}: "
                f"MRP **₹{_format_inr(mrp)}**, PTR **₹{_format_inr(ptr)}**, "
                f"ex-mill **₹{_format_inr(ex_mill)}**"
            )
    header = f"**{query_label}** — Article Master prices:"
    if len(articles) > 12:
        extra = len(articles) - 12
        lines.append(f"…aur {extra} aur variant." if lang == "hi" else f"…and {extra} more variant(s).")
    return header + "\n" + "\n".join(lines)


def _query_item_rows(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    distributor_id: int | None = None,
    season: str | None = None,
    category: str | None = None,
    season_contains: str | None = None,
    min_score: int = 55,
    tokens: list[str] | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT
            fo.id AS order_id,
            fo.season,
            fo.category,
            fo.distributor_id,
            COALESCE(md.firm_name, md.name, fo.distributor_name_raw, 'Unknown') AS distributor_name,
            fi.id AS item_id,
            fi.brand,
            fi.size,
            fi.product_type,
            fi.raw_qty_value,
            fi.detected_unit,
            fi.final_piece_qty,
            fi.ex_mill_price
        FROM filled_order_items fi
        JOIN filled_orders fo ON fo.id = fi.filled_order_id
        LEFT JOIN master_distributors md ON md.id = fo.distributor_id
        WHERE fo.user_id = ?
    """
    params: list[Any] = [user_id]
    if distributor_id is not None:
        query += " AND fo.distributor_id = ?"
        params.append(distributor_id)
    if season:
        query += " AND LOWER(fo.season) LIKE '%' || LOWER(?) || '%'"
        params.append(season.strip())
    elif season_contains:
        query += " AND LOWER(fo.season) LIKE '%' || LOWER(?) || '%'"
        params.append(season_contains.strip())
    if category:
        query += " AND LOWER(fo.category) = LOWER(?)"
        params.append(category.strip())
    try:
        rows = conn.execute(query, params).fetchall()
    except sqlite3.OperationalError:
        # Masters table may be absent (e.g. hop-only DB); still answer from FO rows.
        query = query.replace(
            "LEFT JOIN master_distributors md ON md.id = fo.distributor_id",
            "",
        ).replace(
            "COALESCE(md.firm_name, md.name, fo.distributor_name_raw, 'Unknown') AS distributor_name,",
            "COALESCE(fo.distributor_name_raw, 'Unknown') AS distributor_name,",
        )
        try:
            rows = conn.execute(query, params).fetchall()
        except sqlite3.OperationalError:
            return []
    results: list[dict[str, Any]] = []
    for row in rows:
        item = {
            "order_id": row[0],
            "season": row[1],
            "category": row[2],
            "distributor_id": row[3],
            "distributor_name": row[4],
            "item_id": row[5],
            "brand": row[6],
            "size": row[7],
            "product_type": row[8],
            "raw_qty_value": row[9],
            "detected_unit": row[10],
            "final_piece_qty": row[11],
            "ex_mill_price": row[12],
        }
        if tokens:
            score = _score_item(tokens, item["brand"], item["size"], item["product_type"])
            if score < min_score:
                continue
            item["match_score"] = score
        results.append(item)
    if tokens:
        results.sort(key=lambda r: r.get("match_score", 0), reverse=True)
    return results


def _help_answer(lang: str) -> str:
    if lang == "hi":
        return (
            "Main aapke **Filled Orders**, **Article Master**, **Distributors/Retailers**, aur "
            "**Target vs Achievement** data se jawab deta hoon. Puch sakte ho:\n"
            "• *BND / SUP / PTJ kaun distributor hai?*\n"
            "• *Bernina ne Florentine King bedsheet mein kitna qty order kiya?*\n"
            "• *Jatin Arora kon hai?*\n"
            "• *Aster ki MRP kitni hai?*\n"
            "• *Bernina ka GST number aur address?*\n"
            "• *FY 2024-2025 target achievement kitna hai?*\n"
            "• *Kalra Agencies AW26 Towel total kitna hai?*\n"
            "• *AW26 Towel season mein sab distributors ka total?*\n\n"
            "_Galat jawab aaye to admin **/api/v1/nexora/ask/teach** se naya phrase sikhha sakte hain — "
            "har sawal ke liye code change ki zaroorat nahi._"
        )
    return (
        "I answer from **Filled Orders**, **Article Master**, **party master**, and "
        "**Target vs Achievement**. Try:\n"
        "• *Which distributor is BND / SUP / PTJ?*\n"
        "• *How much qty did Bernina order for Florentine King bedsheet?*\n"
        "• *Who is Jatin Arora?*\n"
        "• *What is Aster MRP?*\n"
        "• *Bernina GST number and full address?*\n"
        "• *FY 2024-2025 target vs achievement?*\n"
        "• *Kalra Agencies AW26 Towel total qty and amount?*\n"
        "• *Season summary for AW26 Towel?*\n\n"
        "_Wrong answer? Teach a new phrase via **POST /api/v1/nexora/ask/teach** — no code deploy needed._"
    )


def _format_item_answer(items: list[dict[str, Any]], lang: str) -> str:
    if not items:
        return (
            "Is sawal ke liye koi matching order line nahi mili."
            if lang == "hi"
            else "No matching order line found for this question."
        )
    lines = []
    for it in items[:5]:
        pcs = float(it.get("final_piece_qty") or 0)
        ex_mill = float(it.get("ex_mill_price") or 0)
        amount = pcs * ex_mill
        product = " / ".join(
            p for p in (it.get("brand"), it.get("size"), it.get("product_type")) if p
        ) or "—"
        raw_part = ""
        if it.get("detected_unit"):
            raw_part = f" ({_format_qty(it.get('raw_qty_value'))} {it.get('detected_unit')})"
        if lang == "hi":
            lines.append(
                f"**{it['distributor_name']}** — {product} ({it.get('season') or '—'}): "
                f"**{_format_qty(pcs)} pcs**{raw_part}, Ex-mill **₹{_format_inr(amount)}**"
            )
        else:
            lines.append(
                f"**{it['distributor_name']}** — {product} ({it.get('season') or '—'}): "
                f"**{_format_qty(pcs)} pcs**{raw_part}, ex-mill **₹{_format_inr(amount)}**"
            )
    if len(items) > 5:
        extra = len(items) - 5
        lines.append(f"…aur {extra} aur line{'s' if extra != 1 else ''}." if lang == "hi" else f"…and {extra} more line(s).")
    return "\n".join(lines)


def _format_distributor_total(
    distributor_name: str,
    season: str | None,
    items: list[dict[str, Any]],
    lang: str,
) -> str:
    if not items:
        msg = f"{distributor_name} ke liye"
        if season:
            msg += f" {season}"
        return msg + (" mein koi saved order nahi mila." if lang == "hi" else " — no saved orders found.")

    total_pcs = sum(float(i.get("final_piece_qty") or 0) for i in items)
    total_amt = sum(
        float(i.get("final_piece_qty") or 0) * float(i.get("ex_mill_price") or 0) for i in items
    )
    seasons = sorted({i.get("season") or "—" for i in items})
    season_txt = season or ", ".join(seasons)
    if lang == "hi":
        return (
            f"**{distributor_name}** ({season_txt}): "
            f"**{_format_qty(total_pcs)} pcs** total, "
            f"**{len(items)}** product lines, "
            f"Ex-mill value **₹{_format_inr(total_amt)}**."
        )
    return (
        f"**{distributor_name}** ({season_txt}): "
        f"**{_format_qty(total_pcs)} pcs** total across "
        f"**{len(items)}** lines, ex-mill value **₹{_format_inr(total_amt)}**."
    )


def _format_season_summary(season: str, overview_rows: list[dict[str, Any]], lang: str) -> str:
    if not overview_rows:
        return (
            f"{season} season mein abhi koi filled order nahi hai."
            if lang == "hi"
            else f"No filled orders found for season {season}."
        )
    lines = []
    total_pcs = 0.0
    total_amt = 0.0
    for row in overview_rows:
        pcs = float(row.get("total_piece_qty") or 0)
        amt = float(row.get("total_ex_mill_value") or 0)
        total_pcs += pcs
        total_amt += amt
        lines.append(
            f"• **{row.get('distributor_name')}** — {_format_qty(pcs)} pcs, ₹{_format_inr(amt)}"
        )
    header = (
        f"**{season}** — sab distributors:"
        if lang == "hi"
        else f"**{season}** — all distributors:"
    )
    footer = (
        f"Grand total: **{_format_qty(total_pcs)} pcs**, Ex-mill **₹{_format_inr(total_amt)}**."
    )
    return header + "\n" + "\n".join(lines) + "\n" + footer


def answer_question(
    conn: sqlite3.Connection,
    user_id: int,
    question: str,
    workspace_id: str | None = None,
    db_path: str | None = None,
    *,
    _skip_learn: bool = False,
    _use_llm: bool = False,
) -> dict[str, Any]:
    """Return { answer, intent, data } for a natural-language question.

    Data isolation:
    - ``house_of_prizm`` workspace → hop_* only (never filled-orders / BD masters)
    - all other workspaces → filled orders / masters scoped by user_id + workspace_id
    """
    ws = (workspace_id or "").strip() or None

    # House of Prizm: dedicated Ask path — no cross-tenant BD data
    if ws == "house_of_prizm":
        import hop_ask

        return hop_ask.answer_question(
            conn,
            user_id,
            question,
            workspace_id=ws,
            db_path=db_path,
            _skip_learn=_skip_learn,
            _use_llm=_use_llm,
        )

    fodb.ensure_schema(conn)
    lang = _detect_lang_hint(question)
    q = _normalize_space(question)
    if not q:
        return {"answer": _help_answer(lang), "intent": "help", "data": {}}

    lower = q.lower()
    if lower in {"help", "?", "hi", "hello", "namaste"} or "kaise puch" in lower:
        return {"answer": _help_answer(lang), "intent": "help", "data": {}}

    original_q = q
    learned_meta: dict[str, Any] | None = None
    if not _skip_learn:
        canonical, learned_meta = learn.find_canonical_question(conn, q, ws, owner_user_id=user_id)
        if canonical and _normalize_space(canonical).lower() != lower:
            result = answer_question(
                conn, user_id, canonical,
                workspace_id=ws, db_path=db_path,
                _skip_learn=True,
            )
            data = dict(result.get("data") or {})
            data["learned"] = learned_meta
            data["original_question"] = original_q
            data["canonical_question"] = canonical
            result["data"] = data
            return result
        # Intentionally do NOT call LLM here — Gemini before rules made every
        # Ask slow (multi-model timeouts) and often rewrote good Hindi questions
        # into help text. LLM runs only as a last-resort below.

    # Hard isolation: never load masters across all workspaces
    if not ws:
        return {
            "answer": "Workspace missing — cannot answer without your workspace scope.",
            "intent": "error",
            "data": {"error": "workspace_required"},
        }

    season = _parse_season(q)
    scope = _parse_scope_hints(q)
    category = scope.get("category")
    season_contains = None if season else scope.get("season_contains")
    fy_label = _parse_fy(q)
    distributors = _load_distributors(conn, ws)
    need_retailers = (
        _is_retailer_question(lower)
        or _wants_identity_info(lower)
        or _wants_party_profile(lower)
    )
    retailers = _load_retailers(conn, ws) if need_retailers else []
    distributor = _find_distributor(q, distributors)
    retailer = _find_retailer(q, retailers) if retailers and (
        _is_retailer_question(lower) or not distributor
    ) else None
    if not retailer and _wants_party_profile(lower):
        if not retailers:
            retailers = _load_retailers(conn, ws)
        retailer = _find_retailer(q, retailers)
    tokens = _product_tokens(q, distributor)
    wants_total = _wants_amount_or_total(lower) or bool(category or season_contains)
    wants_qty = (
        any(h in lower for h in _QTY_HINTS) or (bool(tokens) and not _wants_identity_info(lower))
    )
    has_specific_product = bool(tokens)

    # Article Master MRP / PTR (e.g. "florentine ks bs ki mrp")
    if _wants_article_price_lookup(lower):
        brand_hint, size_hint = _parse_article_brand_size_hints(q, distributor, distributors)
        if brand_hint:
            articles = _query_articles_by_brand(
                conn, user_id, brand_hint, ws, size_hint=size_hint,
            )
            return {
                "answer": _format_article_prices_answer(articles, brand_hint, lang, size_hint),
                "intent": "article_price",
                "data": {"brand_hint": brand_hint, "size_hint": size_hint, "matches": len(articles)},
            }

    # Article Master ex-mill unit price (e.g. "aster ka ex mill kitna hai")
    if _wants_ex_mill_price_lookup(lower):
        brand_hint, size_hint = _parse_article_brand_size_hints(q, distributor, distributors)
        if not brand_hint:
            brand_hint = _extract_brand_hint(q, distributor, distributors)
            size_hint = None
        if brand_hint:
            articles = _query_articles_by_brand(
                conn, user_id, brand_hint, ws, size_hint=size_hint,
            )
            return {
                "answer": _format_article_ex_mill_answer(articles, brand_hint, lang, size_hint),
                "intent": "article_ex_mill",
                "data": {"brand_hint": brand_hint, "size_hint": size_hint, "matches": len(articles)},
            }

    # Nickname → distributor (e.g. "bnd kaun distributor hai", "sup nick")
    nick_dist = _find_distributor_by_nickname(q, distributors)
    short_nick_query = False
    if nick_dist:
        nick_compact = _nick_key(nick_dist.get("firm_nick_name") or "")
        filler = {
            "batao", "bata", "bolo", "info", "information", "details", "detail",
            "jankari", "janakari", "please", "pls", "full",
        }
        extra_tokens = [
            t for t in _question_tokens(q)
            if _nick_key(t) != nick_compact and t not in filler
        ]
        short_nick_query = len(extra_tokens) == 0
    if nick_dist and (
        _wants_nickname_info(lower)
        or _wants_identity_info(lower)
        or short_nick_query
    ):
        # Skip nickname dump when user asked for GST / address / phone etc.
        asked = _requested_party_fields(lower) or set()
        if asked - {"firm_nick_name", "firm_name"}:
            pass
        else:
            return {
                "answer": _format_nickname_answer(nick_dist, lang),
                "intent": "distributor_nickname",
                "data": {
                    "entity_type": "distributor",
                    "id": nick_dist.get("id"),
                    "firm_nick_name": nick_dist.get("firm_nick_name"),
                    "firm_name": nick_dist.get("firm_name"),
                },
            }

    # Who is / identity (e.g. "jatin arora kon hai")
    if _wants_identity_info(lower) and not _wants_party_profile(lower):
        if not retailers:
            retailers = _load_retailers(conn, ws)
        person_hit = _find_person_entity(q, distributors, retailers)
        if person_hit:
            entity, entity_type = person_hit
            return {
                "answer": _format_identity_answer(entity, entity_type, lang),
                "intent": "party_identity",
                "data": {"entity_type": entity_type, "id": entity.get("id")},
            }

    # Target vs Achievement
    if _wants_ta_info(lower):
        ta_result = _answer_ta_question(
            conn, ws, db_path, distributor, fy_label, lang,
        )
        if ta_result:
            return ta_result

    # Distributor / retailer master profile — only the fields asked for
    if _wants_party_profile(lower):
        only_keys = _requested_party_fields(lower)
        if retailer and (_is_retailer_question(lower) or not distributor):
            return {
                "answer": _format_party_profile(retailer, "retailer", lang, only_keys=only_keys),
                "intent": "party_profile",
                "data": {
                    "entity_type": "retailer",
                    "id": retailer.get("id"),
                    "fields": sorted(only_keys) if only_keys else "all",
                },
            }
        if distributor:
            return {
                "answer": _format_party_profile(distributor, "distributor", lang, only_keys=only_keys),
                "intent": "party_profile",
                "data": {
                    "entity_type": "distributor",
                    "id": distributor.get("id"),
                    "fields": sorted(only_keys) if only_keys else "all",
                },
            }

    # Season-wide summary
    if season and not distributor and (wants_total or "sab" in lower or "all" in lower):
        overview = fodb.build_season_overview(conn, user_id)
        rows = next((s.get("rows") or [] for s in overview if s.get("season", "").lower() == season.lower()), [])
        if not rows:
            for s in overview:
                if season.lower() in (s.get("season") or "").lower():
                    rows = s.get("rows") or []
                    season = s.get("season") or season
                    break
        return {
            "answer": _format_season_summary(season, rows, lang),
            "intent": "season_summary",
            "data": {"season": season, "rows": rows},
        }

    # Distributor total (category/season scope — not a specific SKU question)
    if distributor and wants_total and (category or season_contains) and not has_specific_product:
        dist_id = int(distributor["id"])
        items = _query_item_rows(
            conn, user_id,
            distributor_id=dist_id,
            season=season,
            category=category,
            season_contains=season_contains,
        )
        name = _distributor_label(distributor)
        scope_label = season or (season_contains.title() if season_contains else None) or category
        return {
            "answer": _format_distributor_total(name, scope_label, items, lang),
            "intent": "distributor_total",
            "data": {"distributor_id": dist_id, "season": season, "category": category, "line_count": len(items)},
        }

    if distributor and wants_total and not has_specific_product:
        dist_id = int(distributor["id"])
        items = _query_item_rows(conn, user_id, distributor_id=dist_id, season=season)
        name = _distributor_label(distributor)
        return {
            "answer": _format_distributor_total(name, season, items, lang),
            "intent": "distributor_total",
            "data": {"distributor_id": dist_id, "season": season, "line_count": len(items)},
        }

    # Product / line qty (optional distributor)
    if has_specific_product or wants_qty:
        items = _query_item_rows(
            conn,
            user_id,
            distributor_id=int(distributor["id"]) if distributor else None,
            season=season,
            tokens=tokens or None,
            min_score=50 if distributor else 80,
        )
        if not items and distributor:
            items = _query_item_rows(
                conn, user_id, distributor_id=int(distributor["id"]), season=season, tokens=None,
            )
        if items and tokens:
            top_score = items[0].get("match_score", 0)
            items = [i for i in items if i.get("match_score", 0) >= max(80, top_score - 8)][:8]
        return {
            "answer": _format_item_answer(items, lang),
            "intent": "item_qty",
            "data": {"matches": len(items), "season": season},
        }

    if distributor:
        dist_id = int(distributor["id"])
        items = _query_item_rows(conn, user_id, distributor_id=dist_id, season=season)
        name = _distributor_label(distributor)
        return {
            "answer": _format_distributor_total(name, season, items, lang),
            "intent": "distributor_total",
            "data": {"distributor_id": dist_id, "season": season},
        }

    help_result = {
        "answer": _help_answer(lang),
        "intent": "help",
        "data": {"note": "Could not parse distributor or product from question."},
    }
    # Last resort only: rewrite via Gemini, then re-run rules once (never nest LLM).
    if _use_llm and not _skip_learn:
        llm_canonical, _llm_err = learn.llm_suggest_canonical(original_q)
        if llm_canonical and _normalize_space(llm_canonical).lower() != lower:
            result = answer_question(
                conn, user_id, llm_canonical,
                workspace_id=ws, db_path=db_path,
                _skip_learn=True,
                _use_llm=False,
            )
            if result.get("intent") not in ("help", "error", None):
                data = dict(result.get("data") or {})
                data["llm_canonical"] = llm_canonical
                data["original_question"] = original_q
                result["data"] = data
                return result
            help_result["data"]["llm_canonical"] = llm_canonical
            help_result["data"]["llm_fallback_failed"] = True
    return help_result

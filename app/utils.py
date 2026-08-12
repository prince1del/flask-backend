import calendar
import difflib
import os
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any


def auth_enabled() -> bool:
    value = os.getenv("AUTH_ENABLED")
    if value is None:
        return True
    return value.lower() in {"1", "true", "yes", "on"}


def expected_upload_format(key: str) -> dict[str, set[str]]:
    if key in {"order_file", "filled_file"}:
        return {
            "extensions": {".csv", ".xlsx", ".xls", ".xlsm", ".xlsb"},
            "content_types": {
                "text/csv",
                "application/csv",
                "application/vnd.ms-excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/octet-stream",
                "application/x-download",
                "application/x-unknown",
                "binary/octet-stream",
                "multipart/form-data",
                "text/plain",
                "application/xml",
            },
        }

    if key in {"sales_order_file", "invoice_file"}:
        return {
            "extensions": {".pdf"},
            "content_types": {
                "application/pdf",
                "application/octet-stream",
                "application/x-download",
                "binary/octet-stream",
            },
        }

    return {"extensions": set(), "content_types": set()}


def stage_label_for_key(key: str) -> str:
    return {
        "order_file": "Stage 1 - Common order sheet",
        "filled_file": "Stage 2 - Distributor filled order",
        "sales_order_file": "Stage 3 - Sales order",
        "invoice_file": "Stage 4 - Commercial invoice",
    }.get(key, key)


def detect_upload_file_type(filename: str, content_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf" or "pdf" in content_type:
        return "pdf"
    if (
        suffix in {".xlsx", ".xls", ".xlsm", ".xlsb", ".csv"}
        or "excel" in content_type
        or "spreadsheet" in content_type
    ):
        return "excel"
    return "unknown"


def infer_distributor_name(
    upload_key: str, filename: str, explicit_name: str | None = None
) -> str | None:
    if explicit_name and explicit_name.strip():
        return explicit_name.strip()
    if upload_key == "order_file":
        return "Common Order Sheet"

    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    if not stem:
        return None

    ignored_tokens = {
        "filled",
        "order",
        "sheet",
        "sales",
        "so",
        "invoice",
        "commercial",
        "stage1",
        "stage2",
        "stage3",
        "stage4",
    }
    tokens = [token for token in stem.split() if token.lower() not in ignored_tokens]
    candidate = " ".join(tokens).strip()
    return candidate or None


_INTENT_KEYWORDS: dict[str, list[str]] = {
    "pjp": [
        "retailers should i visit",
        "visit today",
        "which retailers",
        "pjp",
        "schedule",
        "today's visits",
        "todays visits",
        "visit list",
        "beat plan",
        "tour plan",
        "route plan",
        "morning suggestion",
        "visit plan",
        "aaj kise milna",
        "aaj kahan jana",
        "aaj ka plan",
        "aaj ka route",
        "kis retailer ke paas jana",
        "kaun se retailer",
        "aaj ki visit",
        "tomorrow pjp",
        "tomorrow visit",
        "kal ka pjp",
        "kal ki visit",
        "kal kaha jana",
        "day after tomorrow",
        "parso ka pjp",
        "monday pjp",
        "tuesday pjp",
        "wednesday pjp",
        "thursday pjp",
        "friday pjp",
        "saturday pjp",
        "sunday pjp",
        "monday visit",
        "tuesday visit",
        "wednesday visit",
        "thursday visit",
        "friday visit",
        "saturday visit",
        "sunday visit",
        "kal ka plan",
        "aaj ka program",
        "kal ka program",
        "mera plan",
        "mera program",
        "my plan",
        "my program",
        "kal ka schedule",
    ],
    "last_visit": [
        "last visit",
        "visited",
        "visit to",
        "recent visit",
        "when did i visit",
        "when was the last",
        "pichli visit",
        "aakhri visit",
        "aakhri baar",
        "kab gaya tha",
        "kab gayi thi",
        "last gaya",
        "kab mila tha",
        "kab milne gaya",
    ],
    "alerts": [
        "mismatch",
        "alert",
        "price mismatch",
        "invoice",
        "alerts",
        "warning",
        "data mismatch",
        "koi alert",
        "koi problem",
        "koi issue",
        "galti hai",
        "error hai",
        "flag hua",
        "kya problem hai",
    ],
    "target": [
        "target",
        "achievement",
        "target vs achievement",
        "kitna target hai",
        "target kitna hai",
        "target achieve",
        "achieve kiya",
        "kitna achieve",
        "kitna target",
        "financial year",
        "kis financial year",
        "konse financial year",
        "konsi financial year",
        "which financial year",
    ],
    "purchase_trends": [
        "top-selling",
        "top selling",
        "purchase",
        "trend",
        "this month",
        "best selling",
        "trending",
        "top article",
        "top product",
        "purchase pattern",
        "sabse zyada bikne wala",
        "sabse zyada bikta hai",
        "kya bikta hai",
        "kya bik raha hai",
        "kharida",
        "order kiya",
    ],
    "credit": [
        "credit limit",
        "outstanding",
        "credit days",
        "credit policy",
        "credit status",
        "account status",
        "pending payment",
        "udhar",
        "baaki paisa",
        "kitna udhar",
        "kitna baaki hai",
        "kitna baki hai",
    ],
    "category_orders": [
        "which category",
        "konsi category",
        "kis category",
        "category wise",
        "category ka order",
        "category mein kitna",
        "categories",
    ],
    "owner": [
        "owner",
        "malik",
        "proprietor",
        "owner ka naam",
        "firm ka malik",
        "kaun hai owner",
        "kiska hai",
        "profile",
        "profile of",
        "ka profile",
        "profile batao",
        "mobile number",
        "mobile no",
        "phone number",
        "phone no",
        "contact number",
        "contact no",
        "mobile kya hai",
        "number kya hai",
        "ka number",
        "ka contact",
        "address",
        "pata",
        "location kya hai",
        "gst number",
        "gst no",
        "gstin",
    ],
}

# Filler/question words stripped when extracting a party (distributor) name
# from a free-text query for intents that need an exact DB name match
# (target, purchase trends) — e.g. "bernina ka target batao" -> "bernina".
_PARTY_QUERY_STOPWORDS = {
    "ka", "ki", "ke", "hai", "batao", "bata", "batado", "batayein",
    "kitna", "kitni", "kya", "please", "tell", "me", "show", "what", "is",
    "are", "the", "of", "for", "target", "targets", "achievement",
    "achievements", "achieve", "achieved", "kiya", "vs", "versus",
    "top-selling", "top", "selling", "best", "design", "trend", "trends",
    "purchase", "purchases", "this", "month", "today", "last", "visit",
    "visited", "to", "and", "how", "much", "give", "get", "hey", "nexora",
    "konsi", "konse", "kis", "kaun", "kaunsa", "kaunsi", "mein", "main",
    "category", "categories", "order", "orders", "wise", "distributor",
    "distributors", "firm", "firms", "naam", "name", "kiska", "kiski",
    "malik", "owner", "proprietor", "who", "which", "in",
    "mobile", "phone", "contact", "number", "no",
    "address", "pata", "location", "gst", "gstin",
    "profile", "mr", "mrs", "ms", "shri", "smt", "shree", "sri",
    "out", "them", "that", "it", "many", "unme", "usme", "inme", "se", "un", "us",
    "mrp", "exmill", "ex-mill", "ex", "mill", "xmill", "x-mill", "x",
    "price", "rate", "aur",
    "product", "products", "article", "size", "batayein",
    "financial", "year", "years", "fy", "kitne", "thi", "tha", "konse",
    "konsi", "was", "were", "did", "have",
    "total", "value", "season",
    "double", "single", "king", "bed", "bath", "sheet", "sheets",
    "bedsheet", "bedsheets", "kids", "kid", "towel", "towels", "large",
    "diya", "diya hai", "hoga", "dedo", "de", "do",
    "ne", "kari", "kare", "kiye", "liya", "liye", "diye", "hua", "hui",
    "hue", "becha", "beche", "bechi", "li", "le", "lo", "lena", "lene",
    "kharida", "kharide", "kharidi", "khareed", "sold", "bought",
}


def extract_party_name_candidate(query: str) -> str:
    """Best-effort extraction of a distributor/retailer name from a
    free-text Ask Nexora query by stripping common filler/question words,
    for intents that need an exact DB name match (target, purchase trends).
    Falls back to the original query if nothing is left after stripping.
    """
    words = re.findall(r"[\w&.'-]+", query or "")
    kept = [w for w in words if w.lower() not in _PARTY_QUERY_STOPWORDS]
    candidate = " ".join(kept).strip()
    return candidate or (query or "").strip()

# Single-word anchors used only for typo-tolerant fallback matching —
# kept separate from the phrase lists above so a near-miss on one word
# (e.g. "alrt", "purchas") doesn't need every multi-word phrase to also
# carry misspelled variants.
_FUZZY_TERM_TO_INTENT: dict[str, str] = {
    "visit": "last_visit",
    "visited": "last_visit",
    "pjp": "pjp",
    "schedule": "pjp",
    "alert": "alerts",
    "alerts": "alerts",
    "mismatch": "alerts",
    "invoice": "alerts",
    "purchase": "purchase_trends",
    "trend": "purchase_trends",
    "trending": "purchase_trends",
    "credit": "credit",
    "outstanding": "credit",
    "target": "target",
    "achievement": "target",
    "category": "category_orders",
    "owner": "owner",
    "malik": "owner",
    "proprietor": "owner",
    "address": "owner",
    "profile": "owner",
    "number": "owner",
    "contact": "owner",
}


def _fuzzy_intent_from_words(normalized: str) -> str | None:
    """Typo-tolerant fallback: catch near-misses like 'alrt' or 'purchas'
    on the single-word anchors above, so small spelling/voice-transcription
    slips don't all fall through to the generic search bucket."""
    for word in normalized.split():
        cleaned = word.strip(".,!?:;\"'()")
        if len(cleaned) < 4:
            continue
        match = difflib.get_close_matches(
            cleaned, _FUZZY_TERM_TO_INTENT.keys(), n=1, cutoff=0.82
        )
        if match:
            return _FUZZY_TERM_TO_INTENT[match[0]]
    return None


def collapse_spelled_out_letters(text: str) -> str:
    """Voice STT sometimes spells short acronyms/nicknames out as separate
    letters — "KAG" heard and transcribed as "k a g". Collapse runs of 2+
    consecutive single-letter tokens back into one word so nickname
    lookups (e.g. Kalra's "KAG") still resolve. Applied once to the whole
    query so every downstream step (intent match, entity extraction)
    benefits.
    """
    tokens = (text or "").split()
    result: list[str] = []
    i = 0
    while i < len(tokens):
        if len(tokens[i]) == 1 and tokens[i].isalpha():
            run = [tokens[i]]
            j = i + 1
            while j < len(tokens) and len(tokens[j]) == 1 and tokens[j].isalpha():
                run.append(tokens[j])
                j += 1
            if len(run) >= 2:
                collapsed = "".join(run)
                # Absorb an immediately-following bare number — season
                # codes like "AW26"/"SS25" get heard as "a w 26"/"s s 25".
                if j < len(tokens) and tokens[j].isdigit():
                    collapsed += tokens[j]
                    j += 1
                result.append(collapsed)
                i = j
                continue
        # A season code that came through as one already-joined short
        # token ("SS", "AW", "FW") still needs its trailing number folded
        # in — "SS 26" -> "SS26" — not just the letter-by-letter case above.
        # Scoped to the actual known season prefixes only: a generic
        # "2 <= len <= 4 letters" test also matched ordinary short words
        # like "ka"/"of"/"FY" whenever a bare year happened to follow
        # ("ka 2026 ka target" -> "ka2026 ka target"), which broke the
        # year-detection regex and polluted distributor-name extraction.
        if (
            tokens[i].lower() in ("aw", "ss", "fw")
            and i + 1 < len(tokens)
            and tokens[i + 1].isdigit()
        ):
            result.append(tokens[i] + tokens[i + 1])
            i += 2
            continue
        result.append(tokens[i])
        i += 1
    return " ".join(result)


_WORD_NUM_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_WORD_NUM_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_WORD_NUM_SCALES = {"thousand": 1_000, "lakh": 100_000, "lac": 100_000, "crore": 10_000_000}
_WORD_NUM_VOCAB = set(_WORD_NUM_ONES) | set(_WORD_NUM_TENS) | set(_WORD_NUM_SCALES) | {"hundred", "and"}

_NUM_WORD_RUN_RE = re.compile(
    r"\b(?:" + "|".join(sorted(_WORD_NUM_VOCAB, key=len, reverse=True)) + r")\b"
    r"(?:[\s-]+\b(?:" + "|".join(sorted(_WORD_NUM_VOCAB, key=len, reverse=True)) + r")\b)*",
    re.IGNORECASE,
)


def _words_to_number(run_text: str) -> int | None:
    """"one hundred fifty" -> 150, "two thousand five hundred" -> 2500.
    Standard accumulate-and-scale parse: ones/tens add into the current
    group, "hundred" multiplies it, thousand/lakh/crore flush the scaled
    group into the running total. Returns None for a run that turns out
    to be only filler ("and") with no actual number word."""
    total = 0
    current = 0
    saw_number = False
    for word in re.split(r"[\s-]+", run_text.lower()):
        if word == "and":
            continue
        if word in _WORD_NUM_ONES:
            current += _WORD_NUM_ONES[word]
            saw_number = True
        elif word in _WORD_NUM_TENS:
            current += _WORD_NUM_TENS[word]
            saw_number = True
        elif word == "hundred":
            current = (current or 1) * 100
            saw_number = True
        elif word in _WORD_NUM_SCALES:
            current = (current or 1) * _WORD_NUM_SCALES[word]
            total += current
            current = 0
            saw_number = True
    total += current
    return total if saw_number else None


def convert_number_words_to_digits(text: str) -> str:
    """Voice STT sometimes transcribes a spoken number as words ("one plus
    one") instead of digits ("1 plus 1") — every downstream numeric parse
    (calculator, target years, MRP amounts, ...) only understands digits,
    so convert spelled-out number words to digit strings up front."""

    def _replace(m: re.Match) -> str:
        value = _words_to_number(m.group())
        return str(value) if value is not None else m.group()

    return _NUM_WORD_RUN_RE.sub(_replace, text or "")


def normalize_voice_query(text: str) -> str:
    """Collapse known voice-transcription slips so every downstream step
    (intent matching, date parsing, entity extraction) sees the corrected
    text, not just the intent classifier. Safe to call multiple times —
    idempotent.
    """
    text = collapse_spelled_out_letters(text or "")
    text = convert_number_words_to_digits(text)
    # "PJP" heard as "BJP" (the political party dominates the recognizer's
    # language model) — nobody is asking Ask Nexora about politics.
    text = re.sub(r"(?i)\bbjp\b", "pjp", text)
    # "kal" (tomorrow) sometimes heard as "cal" — not a real word in this
    # app's context, safe to normalize unconditionally.
    text = re.sub(r"(?i)\bcal\b", "kal", text)
    # Common single-typo misspelling that breaks the "address" keyword
    # match outright (plain substring check, no typo tolerance) — not a
    # real word in this app's context, safe to normalize unconditionally.
    text = re.sub(r"(?i)\badress\b", "address", text)
    # A spoken number sequence that happens to sound like a clock time
    # ("five twenty five") gets heard as "5:25" — Ask Nexora has no
    # clock-time query anywhere, so any H:MM/HH:MM pattern here is really
    # one number that got split. Concatenate digits (keeping MM's leading
    # zero) rather than dropping the colon, so "1:05" -> "105", not "15".
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", r"\1\2", text)
    # STT sometimes glues a short filler word directly onto a following
    # year with no space ("ka 2026" heard as "ka2026"), which breaks both
    # the \b(20\d{2})\b year-detection regex used by the target intent and
    # the stopword-based entity extraction — neither recognizes "ka2026"
    # as "ka" + "2026" since there's no word boundary between the letter
    # and the digit. Scoped to the handful of fillers that precede a year
    # in these queries, not a blanket letter/digit split, so it doesn't
    # touch intentionally-joined tokens like season codes ("aw26").
    text = re.sub(r"(?i)\b(ka|ki|ke|of|is|fy)(20\d{2})\b", r"\1 \2", text)
    # "ex-mill" (a supported price field) gets misheard as "x-meal" by
    # voice STT often enough to need its own fix, same as the exmill/
    # x-mill/xmill spelling variants already recognized downstream.
    text = re.sub(r"(?i)\bx[\s-]?meals?\b", "ex mill", text)
    return text


_PJP_DATE_WORDS = (
    "kal", "aaj", "parso", "tomorrow", "today",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)
_PJP_VISIT_WORDS = (
    "jana", "jaana", "visit", "plan", "program", "kaha", "kahan", "kahi",
    "jagah", "schedule", "pjp", "route", "milna", "go", "going", "where",
)


_MONTH_NAMES: dict[str, int] = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
# "17th August" or "August 17" — either order, an optional ordinal suffix
# on the day number. Word boundaries keep "sep"/"mar" etc. from matching
# inside an unrelated longer word.
_ABSOLUTE_DATE_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(_MONTH_NAMES) + r")\b"
    r"|\b(" + "|".join(_MONTH_NAMES) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)


_PAST_TENSE_PJP_WORDS = (
    "tha", "thi", "gaya", "gayi", "gaye", "was", "were", "went", "did",
)


def _looks_like_past_tense_pjp_query(normalized: str) -> bool:
    """"15 July ko kaha tha main" (where WAS I on 15 July) is asking about
    a visit that already happened, not an upcoming one — the past-tense
    marker ("tha"/"gaya"/"was"/"went") is the signal that distinguishes it
    from "where do I have to go on 17th August"."""
    return any(
        re.search(rf"\b{re.escape(w)}\b", normalized) for w in _PAST_TENSE_PJP_WORDS
    )


def find_absolute_date_in_query(query: str, today, assume_future: bool = True):
    """Parse a spoken absolute date ("17th August", "August 17") out of a
    PJP query into a concrete date. When assume_future is True, rolls
    over to next year if the day/month already passed more than a week
    ago — a PJP ask about an upcoming visit almost always means that. Set
    assume_future=False for a past-tense query ("kaha tha main") where
    the date is meant literally, even if that's months ago this year.
    Returns None if no absolute date is present.
    """
    match = _ABSOLUTE_DATE_RE.search(query or "")
    if not match:
        return None
    if match.group(1):
        day, month_name = int(match.group(1)), match.group(2).lower()
    else:
        month_name, day = match.group(3).lower(), int(match.group(4))
    month = _MONTH_NAMES.get(month_name)
    if not month:
        return None
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        return None
    if assume_future and (today - candidate).days > 7:
        try:
            candidate = date(today.year + 1, month, day)
        except ValueError:
            pass
    return candidate


def _looks_like_pjp_query(normalized: str) -> bool:
    """Fixed keyphrases can't enumerate every natural word order/insertion
    ("kal ka kya plan hai", "kal jana kaha hai mujhe", "jana kaha hai kal").
    If the query names both a date and a visit-ish word, it's PJP —
    checked only after the specific-intent phrase list finds no match, so
    it never overrides a more specific signal (e.g. "last visit ... kal").
    """
    has_date = any(w in normalized for w in _PJP_DATE_WORDS) or bool(
        _ABSOLUTE_DATE_RE.search(normalized)
    )
    has_visit = any(w in normalized for w in _PJP_VISIT_WORDS)
    return has_date and has_visit


_ONES_WORDS = [
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen",
]
_TENS_WORDS = [
    "", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety",
]


def _two_digit_words(n: int) -> str:
    if n < 20:
        return _ONES_WORDS[n]
    tens, ones = divmod(n, 10)
    return f"{_TENS_WORDS[tens]} {_ONES_WORDS[ones]}".strip()


def _three_digit_words(n: int) -> str:
    if n < 100:
        return _two_digit_words(n)
    hundreds, rem = divmod(n, 100)
    tail = f" {_two_digit_words(rem)}" if rem else ""
    return f"{_ONES_WORDS[hundreds]} Hundred{tail}"


def number_to_words_indian(amount) -> str:
    """Full amount spelled out, Indian numbering (Crore/Lakh/Thousand) —
    145001 -> "One Lakh Forty Five Thousand One", not just a rounded
    "1 Lakh" magnitude label."""
    n = int(round(float(amount or 0)))
    if n == 0:
        return "Zero"
    negative = n < 0
    n = abs(n)
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, rest = divmod(n, 1_000)
    parts = []
    if crore:
        parts.append(f"{_three_digit_words(crore)} Crore")
    if lakh:
        parts.append(f"{_three_digit_words(lakh)} Lakh")
    if thousand:
        parts.append(f"{_three_digit_words(thousand)} Thousand")
    if rest:
        parts.append(_three_digit_words(rest))
    return ("Minus " if negative else "") + " ".join(parts)


_SEASON_TOKEN_RE = re.compile(r"\b(aw|ss|fw)\d{2}\b")


_SEASON_ORDER_TRIGGER_WORDS = (
    "order", "value", "li", "liya", "liye", "lena", "lene",
    "kharida", "kharide", "kharidi", "khareed", "becha", "beche",
    "bechi", "purchase", "bought", "sold", "diya", "diye",
)


def _looks_like_season_order_query(normalized: str) -> bool:
    """"aw26 bed ka total order", "bernina ka aw26 ka total bed ka order",
    "choice corner ne kitni aster li hai aw26 main" — a season code
    (AW26/SS25/...) plus any order/purchase-ish word means the user wants
    a season order-value figure, not a generic search. Widened beyond
    just "order"/"value" since "kitni X li hai" (how much X has been
    taken/bought) is just as common a phrasing here."""
    if not _SEASON_TOKEN_RE.search(normalized):
        return False
    return any(
        re.search(rf"\b{re.escape(w)}\b", normalized)
        for w in _SEASON_ORDER_TRIGGER_WORDS
    )


_CONTEXT_FOLLOWUP_PHRASES = (
    "out of them", "out of that", "out of it", "of them how", "in them",
    "unme se", "un mein se", "inme se", "in mein se", "usme se", "us mein se",
)


def _looks_like_context_order_query(normalized: str) -> bool:
    """"out of them how many aster order" — a follow-up naming no season
    or distributor of its own, referring back to a previous answer's
    distributor/order set ("out of them"/"unme se") and asking to narrow
    it down by a product/design word. Routed to the same season_order_value
    handler as a season-coded query, which resolves the missing distributor
    from context and treats a missing season as "across all seasons"."""
    if not any(phrase in normalized for phrase in _CONTEXT_FOLLOWUP_PHRASES):
        return False
    return any(
        re.search(rf"\b{re.escape(w)}\b", normalized)
        for w in _SEASON_ORDER_TRIGGER_WORDS
    )


# A DD-MM-YYYY/DD/MM/YYYY style date must never get read as a price range
# ("17-08-2026" -> lo=17, hi=8) — bail out of price-range detection
# entirely whenever one of these is present in the query.
_DATE_LIKE_RE = re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b")

_PRICE_RANGE_RE = re.compile(
    r"(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*"
    r"(?:-|to|se|aur|~|/)\s*"
    r"(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)"
    r"(?!\s*-\s*\d)",
    re.IGNORECASE,
)


def find_price_range_in_query(query: str) -> tuple[float, float] | None:
    """Detect an MRP/price-range mention anywhere in free text — not just
    a query that IS the range (the older bare "1000-2000" fast path in
    global_search's _parse_mrp_price_range is anchored to the whole
    string, so it can't see "1000 se 2500 ke beech ki bedsheet dikhao").
    Handles the hyphen/"to"/"/"/"~" forms plus Hindi "se"/"aur" ("1000 se
    2500 ke beech", "1000 aur 2500 ke beech")."""
    text = query or ""
    if _DATE_LIKE_RE.search(text):
        return None
    for match in _PRICE_RANGE_RE.finditer(text):
        try:
            lo = float(match.group(1).replace(",", ""))
            hi = float(match.group(2).replace(",", ""))
        except ValueError:
            continue
        if lo < 0 or hi < 0 or hi > 1_000_000:
            continue
        if max(lo, hi) < 10:
            # Too small to plausibly be an MRP figure in this business —
            # likely an unrelated pair of small numbers in the sentence.
            continue
        if lo > hi:
            lo, hi = hi, lo
        return lo, hi
    return None


def _looks_like_price_range_query(normalized: str) -> bool:
    return find_price_range_in_query(normalized) is not None


_CALC_NUM_RE = r"\d[\d,]*\.?\d*"


_CALC_OP_LABELS = {"+": "+", "-": "-", "*": "×", "/": "÷"}


def _calc_op_between(segment: str) -> str | None:
    if re.search(r"\+|\bplus\b|\bjod\b|\bjama\b|\badd\b", segment):
        return "+"
    if re.search(r"\bminus\b|\bghata\b|\bsubtract\b", segment):
        return "-"
    if re.search(r"[*x×]|\bmultiply\b|\bmultiplied\b|\bguna\b|\bgunaa\b|\btimes\b", segment):
        return "*"
    if re.search(r"[/÷]|\bdivide\b|\bdivided\b|\bbhag\b|\bbhaag\b", segment):
        return "/"
    return None


def try_calculator(normalized: str) -> dict | None:
    """Parse an arithmetic/percentage expression out of free text —
    "500 ka 10 percent kitna hoga", "25000 plus 15000", "36000 divided by
    12", or a full chain "5 + 2 + 3 + 4 + 5 + 6". Deliberately does NOT
    treat a bare hyphen as subtraction — that symbol is already used
    elsewhere in this app for an MRP range query ("1000-2000"), so
    subtraction requires an explicit word (minus/ghata/subtract). Returns
    a dict with the parsed operands/operators/result, or None if no
    expression was found.
    """
    matches = list(re.finditer(_CALC_NUM_RE, normalized))
    if len(matches) < 2:
        return None

    percent_hit = re.search(r"%|percent|pratishat", normalized)
    if percent_hit:
        a_match, b_match = matches[0], matches[1]
        a = float(a_match.group().replace(",", ""))
        b = float(b_match.group().replace(",", ""))
        # Whichever of the two numbers sits closer to the %/percent token
        # is the percentage value; the other is the base — handles both
        # "10 percent of 500" and "500 ka 10 percent" orderings.
        pct_pos = percent_hit.start()
        if abs(a_match.end() - pct_pos) <= abs(b_match.end() - pct_pos):
            pct_val, base = a, b
        else:
            pct_val, base = b, a
        return {"op": "percent", "operands": [pct_val, base], "ops": [], "result": (pct_val / 100.0) * base}

    # Walk left to right, applying whatever operator sits between each
    # consecutive pair of numbers to a running total — covers both the
    # simple two-number case and a full chain ("5 + 2 + 3 + 4 + 5 + 6").
    # Stops at the first gap with no recognizable operator, so trailing
    # unrelated numbers in the sentence don't get pulled in.
    operands = [float(matches[0].group().replace(",", ""))]
    ops: list[str] = []
    total = operands[0]
    prev_end = matches[0].end()
    for m in matches[1:]:
        op = _calc_op_between(normalized[prev_end:m.start()])
        if not op:
            break
        value = float(m.group().replace(",", ""))
        if op == "/" and value == 0:
            break
        total = {"+": total + value, "-": total - value, "*": total * value, "/": total / value}[op]
        operands.append(value)
        ops.append(op)
        prev_end = m.end()

    if not ops:
        return None
    return {"op": "arithmetic", "operands": operands, "ops": ops, "result": total}


def _looks_like_calculator_query(normalized: str) -> bool:
    return try_calculator(normalized) is not None


def infer_ai_intent(query: str) -> str:
    normalized = normalize_voice_query(query or "").strip().lower()
    if not normalized:
        return "search"
    for intent, phrases in _INTENT_KEYWORDS.items():
        if any(phrase in normalized for phrase in phrases):
            return intent
    if _looks_like_pjp_query(normalized):
        return "pjp"
    if _looks_like_season_order_query(normalized):
        return "season_order_value"
    if _looks_like_context_order_query(normalized):
        return "season_order_value"
    if _looks_like_price_range_query(normalized):
        return "price_range_articles"
    if _looks_like_calculator_query(normalized):
        return "calculator"
    fuzzy_intent = _fuzzy_intent_from_words(normalized)
    if fuzzy_intent:
        return fuzzy_intent
    return "search"


def ensure_upload_dir(base_dir: str | Path) -> Path:
    path = Path(base_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_monthly_report_data(db_path: str, month: str) -> dict[str, Any]:
    try:
        year, mon = month.split("-")
        start_date = f"{year}-{mon}-01"
        last_day = calendar.monthrange(int(year), int(mon))[1]
        end_date = f"{year}-{mon}-{last_day:02d}"
    except Exception:
        return {
            "distributor_activity": [],
            "total_uploads": 0,
            "total_distributors": 0,
            "verified_count": 0,
            "pending_count": 0,
        }
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            total_uploads = conn.execute(
                "SELECT COUNT(*) FROM distributor_order_uploads WHERE DATE(uploaded_at) BETWEEN ? AND ?",
                (start_date, end_date),
            ).fetchone()[0]
            rows = conn.execute(
                """
                SELECT distributor_name,
                    COUNT(*) as total_uploads,
                    SUM(CASE WHEN stage_key = 'order_file' THEN 1 ELSE 0 END) as stage1,
                    SUM(CASE WHEN stage_key = 'filled_file' THEN 1 ELSE 0 END) as stage2,
                    SUM(CASE WHEN stage_key = 'sales_order_file' THEN 1 ELSE 0 END) as stage3,
                    SUM(CASE WHEN stage_key = 'invoice_file' THEN 1 ELSE 0 END) as stage4,
                    MAX(DATE(uploaded_at)) as last_upload
                FROM distributor_order_uploads
                WHERE DATE(uploaded_at) BETWEEN ? AND ?
                AND distributor_name IS NOT NULL
                AND distributor_name != ''
                GROUP BY distributor_name
                ORDER BY total_uploads DESC
                """,
                (start_date, end_date),
            ).fetchall()
            distributor_activity = [dict(row) for row in rows]
            total_distributors = len(distributor_activity)
            verified_count = sum(
                1
                for r in distributor_activity
                if r["stage1"] and r["stage2"] and r["stage3"] and r["stage4"]
            )
            pending_count = total_distributors - verified_count
    except Exception:
        distributor_activity = []
        total_uploads = total_distributors = verified_count = pending_count = 0
    return {
        "distributor_activity": distributor_activity,
        "total_uploads": total_uploads,
        "total_distributors": total_distributors,
        "verified_count": verified_count,
        "pending_count": pending_count,
    }

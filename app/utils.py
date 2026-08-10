import calendar
import difflib
import os
import sqlite3
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
}

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


def infer_ai_intent(query: str) -> str:
    normalized = (query or "").strip().lower()
    if not normalized:
        return "search"
    for intent, phrases in _INTENT_KEYWORDS.items():
        if any(phrase in normalized for phrase in phrases):
            return intent
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

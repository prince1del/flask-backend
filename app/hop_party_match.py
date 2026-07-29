"""HoP party duplicate / fuzzy matching.

Handles cases like:
  SM Courier  ≈  S.M. Courier  ≈  SM Courior  ≈  SM Logistics
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

# Legal / noise suffixes stripped before compare
_LEGAL_SUFFIX_RE = re.compile(
    r"\b("
    r"private\s+limited|pvt\.?\s*ltd\.?|pvt\.?|ltd\.?|llp|inc\.?|incorp(?:orated)?|"
    r"llc|co\.?|company|corp(?:oration)?|enterprises?|industries|traders?|"
    r"and\s+sons|bros\.?|brothers"
    r")\b",
    re.I,
)

# Logistics / courier family → same token (SM Courier ≈ SM Logistic)
_LOGISTICS_SYNONYMS = {
    "courier": "logistics",
    "courior": "logistics",  # common misspelling
    "courrier": "logistics",
    "logistic": "logistics",
    "logistics": "logistics",
    "cargo": "logistics",
    "transport": "logistics",
    "transports": "logistics",
    "transporter": "logistics",
    "express": "logistics",
    "freight": "logistics",
    "shipping": "logistics",
    "movers": "logistics",
    "packers": "logistics",
}

# Auto-link on import (high confidence). UI confirm uses a lower floor.
AUTO_MATCH_THRESHOLD = 0.86
# Soft-block create only for strong fuzzy hits. 0.72 falsely blocked
# "pawarsuit" ≈ "Pawansut Enterprises" (~78%) — different parties.
WARN_MATCH_THRESHOLD = 0.84


def _clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_party_name(name: str) -> str:
    """Lowercase, strip punctuation/legal suffixes, collapse spaces, synonym-fold."""
    s = _clean(name).lower()
    if not s:
        return ""
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)  # S.M. → S M
    s = _LEGAL_SUFFIX_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens: list[str] = []
    for tok in s.split():
        tok = _LOGISTICS_SYNONYMS.get(tok, tok)
        if tok and tok not in ("and", "the", "of"):
            tokens.append(tok)
    return " ".join(tokens)


def name_fingerprint(name: str) -> str:
    """Alphanumeric-only key: 'S.M. Courier' → 'smlogistics' after synonym fold."""
    return re.sub(r"[^a-z0-9]", "", normalize_party_name(name))


def _levenshtein_ratio(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (ca != cb)))
        prev = cur
    return 1.0 - (prev[-1] / max(len(a), len(b)))


def _token_jaccard(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _short_name_alias_score(na: str, nb: str) -> float:
    """Catch short forms / typos of a longer party name.

    Examples:
      sidhi ≈ Siddhi Chandla
      Siddhi ≈ Siddhi Chandla
      sidhi chandla ≈ Siddhi Chandla
    Avoids ultra-short tokens (sm, ab) matching everything.
    """
    ta, tb = na.split(), nb.split()
    if not ta or not tb:
        return 0.0
    # Put fewer-token side first (the short form).
    if len(ta) > len(tb):
        ta, tb = tb, ta
    if len(ta) > 2:
        return 0.0

    def _fuzzy_token_hit(needle: str, haystack: list[str]) -> float:
        if len(needle) < 4:
            # Exact only for 3-letter names (Ram); skip 1–2 letter codes.
            if len(needle) < 3:
                return 0.0
            return 1.0 if needle in haystack else 0.0
        best = 0.0
        for tok in haystack:
            if abs(len(tok) - len(needle)) > max(2, len(needle) // 3):
                continue
            best = max(best, _levenshtein_ratio(needle, tok))
        return best

    # Every short-side token must land on the aligned long-side token.
    # Single-token short forms match the *first* name/brand only
    # (sidhi ≈ Siddhi Chandla), not every last-name hit (Kumar ≉ Ram Kumar).
    if len(ta) == 1:
        hit = _fuzzy_token_hit(ta[0], [tb[0]])
        if hit < 0.82:
            return 0.0
        return min(0.94, 0.86 + 0.1 * ((hit - 0.82) / 0.18))

    hits = [_fuzzy_token_hit(tok, [tb[i]] if i < len(tb) else tb) for i, tok in enumerate(ta)]
    # Also allow second short token to match any later long token (middle/last name).
    if len(ta) >= 2 and len(tb) >= 2:
        hits[1] = max(hits[1], _fuzzy_token_hit(ta[1], tb[1:]))
    if any(h < 0.82 for h in hits):
        return 0.0
    avg = sum(hits) / len(hits)
    return min(0.97, 0.88 + 0.08 * ((avg - 0.82) / 0.18))


def name_similarity(a: str, b: str) -> float:
    """0..1 similarity with punctuation / synonym awareness."""
    na, nb = normalize_party_name(a), normalize_party_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    fa, fb = name_fingerprint(a), name_fingerprint(b)
    if fa and fa == fb:
        return 0.99
    # Compact Levenshtein (smcourier vs smlogistics after fold → both smlogistics)
    compact = _levenshtein_ratio(fa, fb) if fa and fb else 0.0
    token = _token_jaccard(na, nb)
    spaced = _levenshtein_ratio(na, nb)
    # Prefix boost: same leading token(s) e.g. both start with "sm"
    ta, tb = na.split(), nb.split()
    prefix = 0.0
    if ta and tb and ta[0] == tb[0]:
        prefix = 0.12
        if len(ta) > 1 and len(tb) > 1 and ta[1] == tb[1]:
            prefix = 0.2
    alias = _short_name_alias_score(na, nb)
    score = max(compact, spaced * 0.95, token * 0.9, alias) + (prefix if alias < 0.86 else 0.0)
    return min(1.0, score)


def _mobile_tail(mobile: Any) -> str:
    digits = re.sub(r"\D", "", _clean(mobile))
    return digits[-10:] if len(digits) >= 10 else digits


def score_party_match(
    candidate: dict[str, Any],
    existing: dict[str, Any],
) -> dict[str, Any]:
    """Score candidate vs one existing party row."""
    c_name = _clean(candidate.get("company") or candidate.get("name"))
    e_name = _clean(existing.get("company"))
    sim = name_similarity(c_name, e_name)

    c_gst = _clean(candidate.get("gst_no") or candidate.get("gstin")).upper()
    e_gst = _clean(existing.get("gst_no")).upper()
    gst_hit = bool(c_gst and e_gst and c_gst == e_gst)

    c_mob = _mobile_tail(candidate.get("mobile") or candidate.get("phone"))
    e_mob = _mobile_tail(existing.get("mobile"))
    mob_hit = bool(c_mob and e_mob and len(c_mob) >= 8 and c_mob == e_mob)

    # Weighted confidence 0..100 — strong name alone can auto-link (≥86).
    score = sim * 100.0
    reasons: list[str] = []
    if sim >= 0.99:
        reasons.append("name exact/normalized")
    elif sim >= 0.86:
        reasons.append(f"name similar ({sim:.0%})")
    elif sim >= WARN_MATCH_THRESHOLD:
        reasons.append(f"name fuzzy ({sim:.0%})")
    if gst_hit:
        score = max(score, 98.0)
        reasons.append("GST match")
    if mob_hit:
        score = min(100.0, score + 12.0)
        reasons.append("mobile match")
    score = min(100.0, score)

    return {
        "id": existing.get("id"),
        "party_type": existing.get("_party_type") or existing.get("party_type") or "customer",
        "company": e_name,
        "gst_no": e_gst,
        "mobile": _clean(existing.get("mobile")),
        "score": round(score, 1),
        "name_similarity": round(sim, 3),
        "reasons": reasons,
    }


def find_party_matches(
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    company: str,
    gst_no: str = "",
    mobile: str = "",
    party_type: str | None = None,
    exclude_id: int | None = None,
    exclude_party_type: str | None = None,
    min_score: float = WARN_MATCH_THRESHOLD * 100,
    limit: int = 8,
    list_customers=None,
    list_vendors=None,
) -> list[dict[str, Any]]:
    """Find likely duplicates across hop_customers / hop_vendors."""
    from app import hop_db, hop_ops

    list_customers = list_customers or hop_db.list_customers
    list_vendors = list_vendors or hop_ops.list_vendors

    candidate = {"company": company, "gst_no": gst_no, "mobile": mobile}
    pool: list[dict[str, Any]] = []
    want_cust = party_type in (None, "", "customer", "both")
    want_vend = party_type in (None, "", "vendor", "both")

    if want_cust:
        for r in list_customers(conn, workspace_id) or []:
            row = dict(r)
            row["_party_type"] = "customer"
            if exclude_id and exclude_party_type == "customer" and int(row.get("id") or 0) == int(exclude_id):
                continue
            pool.append(row)
    if want_vend:
        for r in list_vendors(conn, workspace_id) or []:
            row = dict(r)
            row["_party_type"] = "vendor"
            if exclude_id and exclude_party_type == "vendor" and int(row.get("id") or 0) == int(exclude_id):
                continue
            pool.append(row)

    scored = [score_party_match(candidate, row) for row in pool]
    scored = [m for m in scored if float(m["score"]) >= float(min_score)]
    scored.sort(key=lambda m: (-float(m["score"]), str(m.get("company") or "").lower()))
    return scored[:limit]


def resolve_existing_party(
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    company: str,
    gst_no: str = "",
    mobile: str = "",
    prefer_type: str | None = None,
    threshold: float = AUTO_MATCH_THRESHOLD * 100,
    customers_by_key: dict[str, dict] | None = None,
    vendors_by_key: dict[str, dict] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Return (existing_row, match_kind) for import linking.

    match_kind: exact | fingerprint | fuzzy | gst | ''
    Prefers same prefer_type (customer/vendor) when scores tie.
    """
    key = _clean(company).lower()
    if customers_by_key is not None and key in customers_by_key and prefer_type != "vendor":
        return customers_by_key[key], "exact"
    if vendors_by_key is not None and key in vendors_by_key and prefer_type != "customer":
        return vendors_by_key[key], "exact"
    # Cross-type exact name still counts as duplicate for linking within preferred type only
    if prefer_type == "customer" and customers_by_key is not None and key in customers_by_key:
        return customers_by_key[key], "exact"
    if prefer_type == "vendor" and vendors_by_key is not None and key in vendors_by_key:
        return vendors_by_key[key], "exact"

    matches = find_party_matches(
        conn,
        workspace_id,
        company=company,
        gst_no=gst_no,
        mobile=mobile,
        party_type=prefer_type or "both",
        min_score=threshold,
        limit=5,
    )
    if not matches:
        # Soften: try fingerprint-only across preferred dicts
        fp = name_fingerprint(company)
        if fp:
            for d, kind in ((customers_by_key, "customer"), (vendors_by_key, "vendor")):
                if not d:
                    continue
                if prefer_type and prefer_type != kind:
                    continue
                for row in d.values():
                    if name_fingerprint(row.get("company") or "") == fp:
                        return row, "fingerprint"
        return None, ""

    best = matches[0]
    # Re-fetch full row from dicts if possible
    row = None
    if best["party_type"] == "customer" and customers_by_key is not None:
        row = customers_by_key.get(_clean(best["company"]).lower())
        if not row:
            for v in customers_by_key.values():
                if int(v.get("id") or 0) == int(best["id"] or 0):
                    row = v
                    break
    if best["party_type"] == "vendor" and vendors_by_key is not None:
        row = vendors_by_key.get(_clean(best["company"]).lower())
        if not row:
            for v in vendors_by_key.values():
                if int(v.get("id") or 0) == int(best["id"] or 0):
                    row = v
                    break
    if row is None:
        row = {
            "id": best["id"],
            "company": best["company"],
            "gst_no": best.get("gst_no"),
            "mobile": best.get("mobile"),
        }
    kind = "gst" if "GST match" in (best.get("reasons") or []) else "fuzzy"
    if float(best.get("name_similarity") or 0) >= 0.99:
        kind = "fingerprint"
    return row, kind

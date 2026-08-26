"""
Suggest master_distributors from filled-order upload filenames.

Reuses CentralizedDB's known shorthand aliases (BND → Bernina, KAG →
Kalra Agencies, …) and fuzzy matching — same knowledge the rest of
NEXORA already has in party matching / order fulfillment.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from centralized_db_system.db import CentralizedDB

# Filename noise — never use these alone to pick a distributor.
_STEM_STOPWORDS = frozenset({
    "order", "orders", "fo", "filled", "booking", "sheet", "sheets",
    "qty", "quantity", "final", "revised", "additional", "base",
    "bed", "bath", "tob", "pillow", "towel", "towels", "aw26", "ss26",
    "aw25", "ss25", "xlsx", "xls", "csv", "copy", "new", "old",
})


def normalize_filename_stem(filename: str) -> str:
    return " ".join(
        word.strip()
        for word in Path(filename).stem.replace("_", " ").replace("-", " ").split()
        if word.strip()
    ).lower()


def _fold_ascii(text: str) -> str:
    """Lowercase + strip accents (Décor → decor) for filename matching."""
    raw = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in raw if not unicodedata.combining(ch)).lower()


def _significant_tokens(text: str) -> list[str]:
    folded = _fold_ascii(text)
    tokens = re.findall(r"[a-z0-9]+", folded)
    return [t for t in tokens if len(t) >= 4 and t not in _STEM_STOPWORDS]


def _display_name(distributor: dict[str, Any]) -> str:
    # Always prefer proper firm name — never show nick/short name in UI.
    firm = (distributor.get("firm_name") or "").strip()
    if firm:
        return firm
    contact = (distributor.get("name") or "").strip()
    if contact:
        return contact
    return f"Distributor #{distributor.get('id')}"


def _pack_suggestion(distributor: dict[str, Any], match_reason: str, filename_stem: str) -> dict[str, Any]:
    return {
        "id": distributor["id"],
        "name": distributor.get("name"),
        "firm_name": distributor.get("firm_name"),
        "firm_nick_name": distributor.get("firm_nick_name"),
        "display_name": _display_name(distributor),
        "match_reason": match_reason,
        "filename_stem": filename_stem,
    }


def _nick_fold(distributor: dict[str, Any]) -> str:
    return _fold_ascii(distributor.get("firm_nick_name") or "")


def _firm_fold(distributor: dict[str, Any]) -> str:
    return _fold_ascii(
        (distributor.get("firm_name") or "")
        or (distributor.get("name") or "")
    )


def _zone_fold(distributor: dict[str, Any]) -> str:
    return _fold_ascii(distributor.get("zone") or "")


def suggest_distributor_from_filename(
    filename: str,
    workspace_id: str,
    db_path: str | None = None,
    user_id: int | None = None,
) -> dict[str, Any] | None:
    """
    Best-effort distributor guess from upload filename only.
    Returns a compact suggestion dict or None.
    """
    stem = normalize_filename_stem(filename)
    if not stem:
        return None
    stem_fold = _fold_ascii(stem)
    stem_tokens = set(_significant_tokens(stem_fold))

    db = CentralizedDB(db_path) if db_path else CentralizedDB()
    distributors = db.list_master_distributors(
        limit=5000, workspace_id=workspace_id, user_id=user_id
    )

    # 1) firm_nick_name token in filename (BND, KAG, …) — accent-insensitive
    for distributor in distributors:
        nick = _nick_fold(distributor)
        if not nick:
            continue
        nick_tokens = nick.split()
        if (
            stem_fold == nick
            or nick in stem_fold.split()
            or stem_fold.startswith(f"{nick} ")
        ):
            return _pack_suggestion(
                distributor,
                match_reason=f"nickname '{nick}' matched filename",
                filename_stem=stem,
            )
        # Short codes like BND / KAG / DCA (2–4 chars) as whole stem token
        if len(nick) <= 4 and nick in stem_fold.split():
            return _pack_suggestion(
                distributor,
                match_reason=f"nickname '{nick}' matched filename",
                filename_stem=stem,
            )

    # 1b) Leading nick/firm word in filename when unique
    # e.g. file "Balaji haryana.xlsx" + nick "Balaji Home Décor" → Balaji
    leading_hits: list[tuple[dict[str, Any], str]] = []
    for distributor in distributors:
        for label in (_nick_fold(distributor), _firm_fold(distributor)):
            if not label:
                continue
            first = _significant_tokens(label)
            if not first:
                continue
            lead = first[0]
            if lead in stem_tokens:
                leading_hits.append((distributor, lead))
                break
    if leading_hits:
        # Prefer also matching zone token from filename (Balaji + Haryana)
        zone_boosted = [
            (d, lead)
            for d, lead in leading_hits
            if any(zt in stem_tokens for zt in _significant_tokens(_zone_fold(d)))
        ]
        pool = zone_boosted or leading_hits
        unique_ids = {int(d["id"]) for d, _ in pool}
        if len(unique_ids) == 1:
            distributor, lead = pool[0]
            return _pack_suggestion(
                distributor,
                match_reason=f"leading name '{lead}' matched filename",
                filename_stem=stem,
            )

    # 2) distributor_code in filename
    for distributor in distributors:
        code = (distributor.get("distributor_code") or "").strip().lower()
        if code and code in stem_fold.replace(" ", ""):
            return _pack_suggestion(
                distributor,
                match_reason=f"code '{code}' in filename",
                filename_stem=stem,
            )

    # 3) Known founder shorthand → canonical name, then fuzzy match in masters
    for alias_key in (stem_fold, stem, " ".join(_significant_tokens(stem_fold)[:2])):
        canonical = db._canonicalize_known_master_name(alias_key)
        if not canonical:
            continue
        result = db._fuzzy_match_distributor(
            canonical, workspace_id=workspace_id, threshold=85
        )
        if result.get("status") == "matched":
            return _pack_suggestion(
                result["distributor"],
                match_reason=f"alias '{alias_key}' → '{canonical}'",
                filename_stem=stem,
            )

    result = db._fuzzy_match_distributor(stem_fold, workspace_id=workspace_id, threshold=85)
    if result.get("status") == "matched":
        return _pack_suggestion(
            result["distributor"],
            match_reason=f"filename '{stem}'",
            filename_stem=stem,
        )

    # 4) Substring match on firm_name / contact name (folded)
    for distributor in distributors:
        for field in ("firm_name", "name"):
            label = _fold_ascii(distributor.get(field) or "")
            if label and label in stem_fold:
                return _pack_suggestion(
                    distributor,
                    match_reason=f"{field} found in filename",
                    filename_stem=stem,
                )

    return None

"""
Suggest master_distributors from filled-order upload filenames.

Reuses CentralizedDB's known shorthand aliases (BND → Bernina, KAG →
Kalra Agencies, …) and fuzzy matching — same knowledge the rest of
NEXORA already has in party matching / order fulfillment.
"""

from pathlib import Path
from typing import Any

from centralized_db_system.db import CentralizedDB


def normalize_filename_stem(filename: str) -> str:
    return " ".join(
        word.strip()
        for word in Path(filename).stem.replace("_", " ").replace("-", " ").split()
        if word.strip()
    ).lower()


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

    db = CentralizedDB(db_path) if db_path else CentralizedDB()
    distributors = db.list_master_distributors(
        limit=5000, workspace_id=workspace_id, user_id=user_id
    )

    # 1) firm_nick_name token in filename (BND, KAG, …)
    for distributor in distributors:
        nick = (distributor.get("firm_nick_name") or "").strip().lower()
        if not nick:
            continue
        stem_tokens = stem.split()
        if stem == nick or nick in stem_tokens or stem.startswith(f"{nick} "):
            return _pack_suggestion(
                distributor,
                match_reason=f"nickname '{nick}' matched filename",
                filename_stem=stem,
            )

    # 2) distributor_code in filename
    for distributor in distributors:
        code = (distributor.get("distributor_code") or "").strip().lower()
        if code and code in stem.replace(" ", ""):
            return _pack_suggestion(
                distributor,
                match_reason=f"code '{code}' in filename",
                filename_stem=stem,
            )

    # 3) Known founder shorthand → canonical name, then fuzzy match in masters
    canonical = db._canonicalize_known_master_name(stem)
    for reference in (canonical, stem):
        if not reference:
            continue
        result = db._fuzzy_match_distributor(reference, workspace_id=workspace_id, threshold=85)
        if result.get("status") == "matched":
            reason = (
                f"alias '{stem}' → '{canonical}'"
                if reference == canonical and canonical != stem
                else f"filename '{stem}'"
            )
            return _pack_suggestion(result["distributor"], match_reason=reason, filename_stem=stem)

    # 4) Substring match on firm_name / contact name
    for distributor in distributors:
        for field in ("firm_name", "name"):
            label = (distributor.get(field) or "").strip().lower()
            if label and label in stem:
                return _pack_suggestion(
                    distributor,
                    match_reason=f"{field} found in filename",
                    filename_stem=stem,
                )

    return None

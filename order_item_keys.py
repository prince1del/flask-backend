"""Shared item_key helpers for Filled Order ↔ SO/CI reconciliation."""

from __future__ import annotations

import re

_SIZE_CODE_RE = re.compile(
    r"(?:(?<=\d\+\d)|(?<![A-Z0-9]))(SB|DB|DBL|KS|KB|KDB|QB)(?:SET|SETS|BS|FS)?\b",
    re.I,
)


def size_code_only_item_key(item_key: str | None) -> str | None:
    """
    Reduces any item_key's size segment to its 2-letter code (DB/SB/KS/KB),
    so Article-Master-sourced keys ("ASTER|100|DB BS") and SO/CI-PDF-sourced
    keys ("ASTER|100|DB") compare equal. Pass BOTH sides through this before
    comparing — never compare one normalized key against one raw key.
    """
    if not item_key:
        return item_key
    parts = str(item_key).split("|")
    if len(parts) < 3:
        return item_key
    size_match = _SIZE_CODE_RE.search(parts[2].upper())
    if size_match:
        code = size_match.group(1).upper()
        if code == "DBL":
            code = "DB"
        elif code in {"KDB", "QB"}:
            code = "KS"
        parts[2] = code
    return "|".join(parts)


def item_keys_match(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return size_code_only_item_key(left) == size_code_only_item_key(right)


_BRAND_STOP = frozenset({
    "SB", "DB", "DBL", "KS", "KB", "KDB", "QB",
    "SET", "BS", "FS", "SBSET", "DBSET", "KSSET",
})


def line_brand_token(item_name: str | None) -> str:
    """'COTTON COMFORT DB 1+2 …' → COTTON COMFORT; 'FLORA 1+2 DB SET' → FLORA."""
    tokens = [t for t in re.split(r"\s+", str(item_name or "").upper()) if t]
    brand: list[str] = []
    for tok in tokens:
        if tok in _BRAND_STOP or re.match(r"\d+\+\d", tok):
            break
        if tok.isdigit() or re.fullmatch(r"\d+CM", tok):
            continue
        brand.append(tok)
        if len(brand) >= 2:
            break
    return " ".join(brand)


def line_brands_match(left_name: str | None, right_name: str | None) -> bool:
    """Same collection only. Flora must never merge into Cotton Comfort."""
    left = line_brand_token(left_name)
    right = line_brand_token(right_name)
    if not left or not right:
        return False
    return left.split()[0] == right.split()[0]

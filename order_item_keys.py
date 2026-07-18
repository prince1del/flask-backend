"""Shared item_key helpers for Filled Order ↔ SO/CI reconciliation."""

from __future__ import annotations

import re

_SIZE_CODE_RE = re.compile(r"\b(DB|SB|KS|KB)\b", re.I)


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
        parts[2] = size_match.group(1).upper()
    return "|".join(parts)


def item_keys_match(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return size_code_only_item_key(left) == size_code_only_item_key(right)

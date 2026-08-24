"""Shared Gemini model configuration for OCR + AI-agent features."""

from __future__ import annotations

import os

# Newer/preview models (gemini-3.5-flash, gemini-flash-latest) have much
# tighter free-tier quotas than the established gemini-2.0-flash /
# gemini-1.5-flash (15 RPM on free tier) — try those first so a busy day
# doesn't hit 429s as fast. The caller's retry loop falls through this whole
# list on both 404 (model unavailable) and 429 (quota exceeded), so the
# newer models still get tried as a last resort if the established ones are
# also exhausted.
DEFAULT_GEMINI_FLASH_MODEL = "gemini-2.0-flash"


def get_ocr_gemini_models() -> tuple[str, ...]:
    """Return ordered model candidates for OCR / AI-agent calls.

    Priority:
    1) GEMINI_OCR_MODELS (comma-separated list)
    2) GEMINI_FLASH_MODEL (single value)
    3) default pinned model + safe fallback aliases
    """
    csv_models = (os.getenv("GEMINI_OCR_MODELS") or "").strip()
    if csv_models:
        models = tuple(m.strip() for m in csv_models.split(",") if m.strip())
        if models:
            return models

    single_model = (os.getenv("GEMINI_FLASH_MODEL") or "").strip()
    if single_model:
        return (single_model,)

    return (
        DEFAULT_GEMINI_FLASH_MODEL,
        "gemini-1.5-flash",
        "gemini-flash-latest",
        "gemini-3.5-flash",
    )

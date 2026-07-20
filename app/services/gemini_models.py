"""Shared Gemini model configuration for OCR features."""

from __future__ import annotations

import os

# Pinned GA model for OCR use-cases.
DEFAULT_GEMINI_FLASH_MODEL = "gemini-3.5-flash"


def get_ocr_gemini_models() -> tuple[str, ...]:
    """Return ordered model candidates for OCR calls.

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
        "gemini-flash-latest",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    )

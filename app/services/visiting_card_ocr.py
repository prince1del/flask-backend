"""Visiting / business card OCR → structured CRM fields.

Uses Gemini Vision when GEMINI_API_KEY is set (best on fancy fonts/colours).
Heavy local OCR (EasyOCR/Paddle) is OFF on Render — those models OOM kill
Starter (512MB). Optional light local OCR only if HOP_CARD_LOCAL_OCR=1.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app.services.gemini_models import get_ocr_gemini_models


def _s(v: Any) -> str:
    return str(v or "").strip()


def _load_dotenv_once() -> None:
    if getattr(_load_dotenv_once, "_done", False):
        return
    _load_dotenv_once._done = True  # type: ignore[attr-defined]
    try:
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if not env_path.exists():
            return
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


def _gemini_key() -> str:
    _load_dotenv_once()
    for name in ("GEMINI_API_KEY", "GOOGLE_GEMINI_API_KEY"):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    try:
        from flask import has_request_context, current_app

        if has_request_context():
            val = (current_app.config.get("GEMINI_API_KEY") or "").strip()
            if val:
                return val
    except Exception:
        pass
    return ""


def _mime_for(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".gif": "image/gif",
    }.get(ext, "image/jpeg")


def _image_b64_jpeg(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    mime = _mime_for(path)
    try:
        from PIL import Image

        img = Image.open(path)
        try:
            from PIL import ImageOps

            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        img = img.convert("RGB")
        w, h = img.size
        max_side = 1800
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
    except Exception:
        return base64.b64encode(raw).decode("ascii"), mime


_EMPTY_FIELDS = {
    "company": "",
    "contact_person": "",
    "mobile": "",
    "email": "",
    "city": "",
    "address": "",
    "customer_type": "",
    "gst_no": "",
    "pan": "",
    "website": "",
    "designation": "",
    "remarks": "",
}


def _normalize_fields(data: dict[str, Any] | None) -> dict[str, str]:
    out = dict(_EMPTY_FIELDS)
    if not isinstance(data, dict):
        return out
    aliases = {
        "company": ("company", "company_name", "organisation", "organization", "business", "firm"),
        "contact_person": ("contact_person", "name", "person", "contact", "full_name"),
        "mobile": ("mobile", "phone", "phone_number", "tel", "mobile_number", "contact_number"),
        "email": ("email", "email_id", "e_mail", "mail"),
        "city": ("city", "town", "location_city"),
        "address": ("address", "full_address", "office_address", "street"),
        "customer_type": ("customer_type", "type", "industry", "category"),
        "gst_no": ("gst_no", "gstin", "gst", "gst_number"),
        "pan": ("pan", "pan_no", "pan_number"),
        "website": ("website", "web", "url", "site"),
        "designation": ("designation", "title", "role", "job_title"),
        "remarks": ("remarks", "notes", "other", "extra"),
    }
    for field, keys in aliases.items():
        for key in keys:
            if key in data and _s(data.get(key)):
                out[field] = _s(data.get(key))
                break
    if out["mobile"]:
        cleaned = re.sub(r"[^\d+]", "", out["mobile"])
        if len(re.sub(r"\D", "", cleaned)) >= 8:
            out["mobile"] = cleaned
    if out["email"]:
        out["email"] = out["email"].lower()
    if out["gst_no"]:
        out["gst_no"] = out["gst_no"].upper().replace(" ", "")
    if out["pan"]:
        out["pan"] = out["pan"].upper().replace(" ", "")
    return out


def _parse_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return None


def _friendly_gemini_error(raw: str) -> str:
    """Turn API JSON blobs into a short user-facing message."""
    text = (raw or "").strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            data = json.loads(text)
            err = data.get("error")
            if isinstance(err, dict):
                return str(err.get("message") or err.get("status") or text)[:160]
        except Exception:
            pass
    return text[:160]


def _extract_gemini(path: Path) -> tuple[dict[str, str], str]:
    key = _gemini_key()
    if not key:
        return dict(_EMPTY_FIELDS), ""
    try:
        b64, mime = _image_b64_jpeg(path)
    except Exception:
        return dict(_EMPTY_FIELDS), ""

    prompt = """You are an expert visiting-card / business-card reader.
Read EVERY visible detail on this card — any font, colour, layout, language (English/Hindi/Hinglish).
Return ONLY a JSON object (no markdown) with these keys:
{
  "company": "",
  "contact_person": "",
  "designation": "",
  "mobile": "",
  "email": "",
  "website": "",
  "address": "",
  "city": "",
  "gst_no": "",
  "pan": "",
  "customer_type": "",
  "remarks": ""
}

Rules:
- Prefer the largest/brand name as company
- contact_person is a person's name (not the company)
- mobile: primary phone with country code if shown (+91…)
- Put extra phones, fax, taglines, products into remarks
- customer_type: Hotel / Designer / Architect / Consultant / Vendor / Dealer / Other if inferable
- Do NOT invent values that are not on the card — use "" when unknown
- Preserve GSTIN / PAN exactly when present
"""

    models = get_ocr_gemini_models()
    last_err = ""
    for model in models:
        body = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {"inlineData": {"mimeType": mime, "data": b64}},
                    ]
                }
            ],
            "generationConfig": {"temperature": 0.05, "maxOutputTokens": 2048},
        }
        payload = json.dumps(body).encode("utf-8")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={key}"
        )
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=28) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            cands = data.get("candidates") or []
            if not cands:
                last_err = "no_candidates"
                continue
            parts = (cands[0].get("content") or {}).get("parts") or []
            text = ""
            for part in parts:
                if isinstance(part, dict) and part.get("text"):
                    text += part["text"]
            parsed = _parse_json_object(text)
            if parsed:
                fields = _normalize_fields(parsed)
                if fields.get("company") or fields.get("contact_person") or fields.get("mobile") or fields.get("email"):
                    return fields, f"gemini_vision:{model}"
            last_err = "unparsed"
        except urllib.error.HTTPError as exc:
            try:
                last_err = _friendly_gemini_error(
                    exc.read().decode("utf-8", errors="ignore")
                )
            except Exception:
                last_err = str(exc)
            # Skip unavailable models (404) and try the next one.
            if exc.code == 404:
                continue
        except Exception as exc:
            last_err = str(exc)
    _extract_gemini.last_error = last_err or "gemini_failed"  # type: ignore[attr-defined]
    return dict(_EMPTY_FIELDS), last_err or "gemini_failed"


_extract_gemini.last_error = ""  # type: ignore[attr-defined]


def _light_local_ocr_allowed() -> bool:
    """RapidOCR + Tesseract — off on Render Starter unless explicitly enabled (OOM risk)."""
    flag = (os.getenv("HOP_CARD_LOCAL_OCR") or "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    if flag in {"1", "true", "yes", "on"}:
        return True
    if (os.getenv("RENDER") or "").strip().lower() in {"true", "1"}:
        return False
    return True


def _local_ocr_text(path: Path) -> tuple[str, str]:
    """Light local OCR (RapidOCR/Tesseract) — never loads EasyOCR/Paddle."""
    if not _light_local_ocr_allowed():
        return "", ""
    try:
        from app.hop_handwriting_ocr import (
            _cleanup_temps,
            _extract_rapid_text,
            _extract_tesseract_text,
            _prepare_image_variants,
        )

        variants = _prepare_image_variants(path)
        try:
            engines = (
                ("rapidocr", _extract_rapid_text),
                ("tesseract", _extract_tesseract_text),
            )
            best_text = ""
            best_name = ""
            for name, fn in engines:
                try:
                    for variant in variants:
                        text = (fn(variant) or "").strip()
                        if len(text) > len(best_text):
                            best_text = text
                            best_name = name
                except Exception:
                    continue
            return best_text, best_name
        finally:
            _cleanup_temps(variants, path)
    except MemoryError:
        return "", "oom"
    except Exception:
        return "", ""


def _heuristic_from_text(text: str) -> dict[str, str]:
    fields = dict(_EMPTY_FIELDS)
    if not text:
        return fields
    lines = [ln.strip() for ln in text.replace("\r", "\n").split("\n") if ln.strip()]
    email_re = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
    phone_re = re.compile(
        r"(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)?\d{5,10}(?:[\s-]?\d{3,6})?"
    )
    gst_re = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b", re.I)
    pan_re = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
    web_re = re.compile(r"(?:https?://)?(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}(?:/\S*)?", re.I)

    emails = email_re.findall(text)
    if emails:
        fields["email"] = emails[0].lower()

    gst = gst_re.search(text)
    if gst:
        fields["gst_no"] = gst.group(0).upper()

    pan = pan_re.search(text)
    if pan and not fields["gst_no"].startswith(pan.group(0)):
        candidate = pan.group(0).upper()
        if candidate not in (fields["gst_no"] or ""):
            fields["pan"] = candidate

    webs = [w for w in web_re.findall(text) if "@" not in w]
    if webs:
        fields["website"] = webs[0]

    phones: list[str] = []
    for m in phone_re.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 10 <= len(digits) <= 13:
            phones.append(m.group(0).strip())
    if phones:
        fields["mobile"] = re.sub(r"[^\d+]", "", phones[0])

    name_candidates: list[str] = []
    for ln in lines[:12]:
        low = ln.lower()
        if "@" in ln or web_re.search(ln):
            continue
        if phone_re.fullmatch(ln.replace(" ", "")):
            continue
        if len(ln) < 2 or len(ln) > 80:
            continue
        if any(x in low for x in ("gst", "pan", "cin", "phone", "mobile", "tel", "fax", "email")):
            continue
        name_candidates.append(ln)

    if name_candidates:
        fields["company"] = name_candidates[0]
        if len(name_candidates) > 1:
            second = name_candidates[1]
            if len(second.split()) <= 5:
                fields["contact_person"] = second
            if len(name_candidates) > 2 and not fields["address"]:
                fields["address"] = ", ".join(name_candidates[2:5])

    city_hint = re.search(
        r"\b(Mumbai|Delhi|Noida|Gurgaon|Gurugram|Pune|Bengaluru|Bangalore|Hyderabad|Chennai|"
        r"Kolkata|Ahmedabad|Jaipur|Chandigarh|Lucknow|Indore|Surat|Nagpur|Goa|"
        r"New Delhi|Faridabad|Ghaziabad)\b",
        text,
        re.I,
    )
    if city_hint:
        fields["city"] = city_hint.group(1)

    leftover = []
    if phones[1:]:
        leftover.append("Also: " + ", ".join(phones[1:3]))
    if leftover:
        fields["remarks"] = "; ".join(leftover)
    return _normalize_fields(fields)


def scan_visiting_card(path: Path) -> dict[str, Any]:
    """Return structured card fields + engine metadata. Never auto-saves.

    On Render: Gemini Vision only (local OCR opt-in via HOP_CARD_LOCAL_OCR=1).
    """
    try:
        return _scan_visiting_card_impl(path)
    except MemoryError:
        return {
            "fields": dict(_EMPTY_FIELDS),
            "engine": "oom",
            "note": (
                "Server memory full — card read fail. Render pe GEMINI_API_KEY set karo "
                "(best fix). 1 minute wait karke dubara try karo."
            ),
            "raw_text_preview": "",
            "confidence": "low",
            "gemini_configured": bool(_gemini_key()),
            "gemini_error": "out_of_memory",
        }
    except Exception as exc:
        return {
            "fields": dict(_EMPTY_FIELDS),
            "engine": "error",
            "note": f"Card read error: {exc}. Fields manually bharo ya dubara try karo.",
            "raw_text_preview": "",
            "confidence": "low",
            "gemini_configured": bool(_gemini_key()),
            "gemini_error": str(exc)[:200],
        }


def _scan_visiting_card_impl(path: Path) -> dict[str, Any]:
    fields, engine = _extract_gemini(path)
    raw_text = ""
    note = ""
    gemini_on = bool(_gemini_key())

    has_core = bool(
        fields.get("company")
        or fields.get("contact_person")
        or fields.get("mobile")
        or fields.get("email")
    )
    gemini_err = getattr(_extract_gemini, "last_error", "") or ""

    if not has_core:
        raw_text, local_engine = _local_ocr_text(path)
        if raw_text:
            fields = _heuristic_from_text(raw_text)
            engine = local_engine or engine or "local_heuristic"
            note = "Local OCR used. Har field verify karke save karo."
            has_core = bool(
                fields.get("company")
                or fields.get("contact_person")
                or fields.get("mobile")
                or fields.get("email")
            )
        if not has_core:
            engine = engine or ("gemini_unavailable" if not gemini_on else "gemini_empty")
            if not gemini_on:
                note = (
                    "GEMINI_API_KEY Render pe set nahi hai — isliye cloud OCR nahi chal raha. "
                    "Render → Environment → GEMINI_API_KEY add karo, redeploy karo. "
                    "Tab tak fields manually bharo."
                )
            elif gemini_err and gemini_err not in ("gemini_failed", "gemini_empty", "unparsed", "no_candidates"):
                note = (
                    f"AI read fail ({_friendly_gemini_error(gemini_err)}). "
                    "Dobara sharp photo lo ya fields manually bharo."
                )
            else:
                note = (
                    "Card se data clear nahi pada. Light / focus check karo, "
                    "phir dubara photo lo — ya neeche fields manually bharo."
                )
        if not (
            fields.get("company")
            or fields.get("contact_person")
            or fields.get("mobile")
            or fields.get("email")
        ):
            if not note:
                note = "Could not read enough detail. Try a sharper photo, or fill fields manually."
    else:
        note = "AI read complete — please verify every field before saving."

    extras = []
    if fields.get("designation"):
        extras.append(f"Title: {fields['designation']}")
    if fields.get("website"):
        extras.append(f"Web: {fields['website']}")
    if extras:
        joined = " · ".join(extras)
        fields["remarks"] = f"{fields['remarks']} | {joined}".strip(" |") if fields.get("remarks") else joined

    return {
        "fields": fields,
        "engine": engine or "none",
        "note": note,
        "raw_text_preview": (raw_text or "")[:800],
        "confidence": "high" if str(engine).startswith("gemini_vision") else ("medium" if has_core else "low"),
        "gemini_configured": gemini_on,
        "gemini_error": (gemini_err or "")[:200] if not str(engine).startswith("gemini_vision") else "",
    }


def save_upload_temp(file_storage) -> Path:
    suffix = Path(getattr(file_storage, "filename", "") or "card.jpg").suffix or ".jpg"
    if suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}:
        suffix = ".jpg"
    fd, name = tempfile.mkstemp(prefix="hop_card_", suffix=suffix)
    os.close(fd)
    path = Path(name)
    file_storage.save(path)
    return path

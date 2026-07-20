"""Multi-engine handwriting OCR for HoP rate-sheet photos.

Priority (best → fallback):
  1. Gemini Vision (structured rates) — strongest on messy handwriting when GEMINI_API_KEY set
  2. Azure Document Intelligence prebuilt-read (+ layout)
  3. EasyOCR (local)
  4. PaddleOCR (local)
  5. RapidOCR (local ONNX)
  6. Tesseract (optional)

Never invents demo/sample rates — only what engines read from the file.
"""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from app.services.gemini_models import get_ocr_gemini_models


def _s(v: Any) -> str:
    return str(v or "").strip()


def _load_dotenv_once() -> None:
    if getattr(_load_dotenv_once, "_done", False):
        return
    _load_dotenv_once._done = True  # type: ignore[attr-defined]
    try:
        env_path = Path(__file__).resolve().parents[1] / ".env"
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
    return ""


def _prepare_image_variants(path: Path) -> list[Path]:
    """Original + contrast-enhanced PNG for local OCRs."""
    paths = [path]
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps

        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) < 1400:
            scale = 1400 / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        gray = ImageOps.grayscale(img)
        gray = ImageOps.autocontrast(gray, cutoff=1)
        gray = ImageEnhance.Contrast(gray).enhance(1.35)
        gray = ImageEnhance.Sharpness(gray).enhance(1.5)
        gray = gray.filter(ImageFilter.MedianFilter(size=3))
        fd, name = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        out = Path(name)
        gray.convert("RGB").save(out, format="PNG")
        paths.append(out)
    except Exception:
        pass
    return paths


def _cleanup_temps(paths: list[Path], original: Path) -> None:
    for p in paths:
        if p != original and p.exists():
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


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


def extract_rates_gemini_vision(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Ask Gemini to read handwritten/printed rate slip → structured lines."""
    key = _gemini_key()
    if not key:
        return [], ""
    try:
        # Resize large phone photos to keep request under ~4MB
        raw = path.read_bytes()
        try:
            from PIL import Image
            import io

            img = Image.open(path)
            img = img.convert("RGB")
            w, h = img.size
            max_side = 1600
            if max(w, h) > max_side:
                scale = max_side / max(w, h)
                img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            raw = buf.getvalue()
            mime = "image/jpeg"
        except Exception:
            mime = _mime_for(path)
        b64 = base64.b64encode(raw).decode("ascii")
    except Exception:
        return [], ""

    prompt = """You are an OCR expert for supplier rate slips (handwritten or printed).
Extract EVERY product rate line from this image.
Return ONLY a JSON array (no markdown, no commentary). Each object:
{"product_name":"...","size":"... or null","rate":number,"gst_pct":number}

Rules:
- rate is the unit price (not qty, not HSN, not serial number)
- gst_pct is usually 5 or 18; if missing use 5
- size like 72x108, 20x30, Free Size when visible
- Keep product names short (Bed Sheet, D/Cover, Bath Mat, Hand towel, Bathrobe, …)
- Do NOT invent products that are not on the slip
- If nothing readable, return []
"""

    models = get_ocr_gemini_models()
    last_err = ""
    for model in models:
        # REST accepts both snake_case and camelCase; send camelCase (official REST style)
        body = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {"inlineData": {"mimeType": mime, "data": b64}},
                    ]
                }
            ],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048},
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
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            # Blocked / safety
            cands = data.get("candidates") or []
            if not cands:
                last_err = "no_candidates"
                continue
            parts = (cands[0].get("content") or {}).get("parts") or []
            text = ""
            for part in parts:
                if isinstance(part, dict) and part.get("text"):
                    text += part["text"]
            rows = _parse_json_rate_array(text)
            if rows:
                return rows, f"gemini_vision:{model}"
            # Sometimes model returns prose — keep trying
            if text.strip():
                last_err = "unparsed_text"
            else:
                last_err = "empty_text"
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                detail = str(exc)
            last_err = f"http_{exc.code}:{detail[:180]}"
            continue
        except Exception as exc:
            last_err = f"{type(exc).__name__}:{exc}"
            continue
    # Stash last error for diagnostics (no secrets)
    extract_rates_gemini_vision.last_error = last_err  # type: ignore[attr-defined]
    return [], ""


extract_rates_gemini_vision.last_error = ""  # type: ignore[attr-defined]


def _parse_json_rate_array(text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    # Find array span
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        arr = json.loads(cleaned[start : end + 1])
    except Exception:
        return []
    if not isinstance(arr, list):
        return []
    out: list[dict[str, Any]] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        name = _s(item.get("product_name") or item.get("product") or item.get("name"))
        rate = item.get("rate")
        try:
            rate_f = float(rate)
        except Exception:
            continue
        if not name or rate_f <= 0 or rate_f > 20000:
            continue
        gst = item.get("gst_pct", item.get("gst", 5))
        try:
            gst_f = float(gst)
        except Exception:
            gst_f = 5.0
        if gst_f > 40:
            gst_f = 5.0
        size = _s(item.get("size")) or None
        out.append(
            {
                "product_name": name,
                "size": size,
                "rate": rate_f,
                "gst_pct": gst_f,
            }
        )
    return out


def _extract_azure_models(path: Path) -> str:
    endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
    key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")
    if not endpoint or not key:
        return ""
    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential
    except Exception:
        return ""
    try:
        client = DocumentIntelligenceClient(endpoint, AzureKeyCredential(key))
        chunks: list[str] = []
        # prebuilt-read is strongest for print + handwriting
        for model_id in ("prebuilt-read", "prebuilt-layout"):
            try:
                with path.open("rb") as payload:
                    poller = client.begin_analyze_document(
                        model_id,
                        analyze_request=payload,
                        content_type="application/octet-stream",
                    )
                result = poller.result()
                content_text = getattr(result, "content", "")
                if isinstance(content_text, str) and content_text.strip():
                    chunks.append(content_text.strip())
                    continue
                lines: list[str] = []
                for page in getattr(result, "pages", []) or []:
                    for line in getattr(page, "lines", []) or []:
                        content = getattr(line, "content", "")
                        if content:
                            lines.append(content)
                if lines:
                    chunks.append("\n".join(lines))
            except Exception:
                continue
        # Prefer longest extraction
        chunks.sort(key=len, reverse=True)
        return chunks[0] if chunks else ""
    except Exception:
        return ""


_EASYOCR_READER = None
_PADDLE_OCR = None


def _get_easyocr():
    global _EASYOCR_READER
    if _EASYOCR_READER is False:
        return None
    if _EASYOCR_READER is not None:
        return _EASYOCR_READER
    try:
        import easyocr

        _EASYOCR_READER = easyocr.Reader(["en"], gpu=False, verbose=False)
        return _EASYOCR_READER
    except Exception:
        _EASYOCR_READER = False
        return None


def _extract_easyocr_text(path: Path) -> str:
    reader = _get_easyocr()
    if reader is None:
        return ""
    try:
        result = reader.readtext(str(path), detail=1, paragraph=False)
        lines: list[str] = []
        for item in result or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                txt = _s(item[1])
                conf = float(item[2]) if len(item) > 2 else 1.0
                if txt and conf >= 0.15:
                    lines.append(txt)
        return "\n".join(lines)
    except Exception:
        return ""


def _get_paddle():
    global _PADDLE_OCR
    if _PADDLE_OCR is False:
        return None
    if _PADDLE_OCR is not None:
        return _PADDLE_OCR
    try:
        from paddleocr import PaddleOCR

        # API varies by version
        try:
            _PADDLE_OCR = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
        except TypeError:
            _PADDLE_OCR = PaddleOCR(lang="en")
        return _PADDLE_OCR
    except Exception:
        _PADDLE_OCR = False
        return None


def _extract_paddle_text(path: Path) -> str:
    engine = _get_paddle()
    if engine is None:
        return ""
    try:
        result = engine.ocr(str(path), cls=True)
        lines: list[str] = []
        # result: list per page → list of [box, (text, conf)]
        pages = result or []
        if pages and isinstance(pages[0], dict):
            # newer API
            for page in pages:
                for line in page.get("rec_texts") or []:
                    if _s(line):
                        lines.append(_s(line))
        else:
            for page in pages:
                if not page:
                    continue
                for row in page:
                    if not row or len(row) < 2:
                        continue
                    info = row[1]
                    if isinstance(info, (list, tuple)) and info:
                        txt = _s(info[0])
                        if txt:
                            lines.append(txt)
        return "\n".join(lines)
    except Exception:
        return ""


def _extract_rapid_text(path: Path) -> str:
    try:
        from rapidocr import RapidOCR
    except Exception:
        return ""
    try:
        engine = RapidOCR()
        result = engine(str(path))
        texts: list[str] = []
        if result is None:
            return ""
        if hasattr(result, "txts") and result.txts:
            texts = [_s(t) for t in result.txts if _s(t)]
        elif isinstance(result, (list, tuple)):
            if result and isinstance(result[0], (list, tuple)) and len(result) >= 2 and isinstance(result[1], (list, tuple)):
                texts = [_s(t) for t in result[1] if _s(t)]
            else:
                for item in result:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        texts.append(_s(item[1]))
        return "\n".join(t for t in texts if t)
    except Exception:
        return ""


def _extract_tesseract_text(path: Path) -> str:
    try:
        import pytesseract
        from PIL import Image, ImageOps
    except Exception:
        return ""
    cmd = os.getenv("TESSERACT_CMD", "").strip()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        return (pytesseract.image_to_string(img, lang="eng", config="--psm 6") or "").strip()
    except Exception:
        return ""


def _score_ocr_text(text: str, parse_fn: Callable[[str], list]) -> tuple[int, list]:
    if not text:
        return 0, []
    try:
        rows = parse_fn(text) or []
    except Exception:
        rows = []
    return len(rows), rows


def run_handwriting_ocr(
    path: Path,
    *,
    parse_text_fn: Callable[[str], list] | None = None,
) -> dict[str, Any]:
    """Run all available handwriting OCR engines; return best structured result.

    Returns:
      {
        lines: [...],           # best structured rows if any
        text: str,              # best raw OCR text
        method: str,
        engines_tried: [...],
        engine_scores: {name: line_count},
      }
    """
    from app.hop_rate_upload import parse_handwritten_ocr_text, parse_rate_lines_from_text

    parse_fn = parse_text_fn or (
        lambda t: parse_handwritten_ocr_text(t) or parse_rate_lines_from_text(t)
    )

    # Higher = preferred when line-count ties (Gemini structured > local OCR noise)
    priority = {
        "gemini_vision": 100,
        "azure_read": 80,
        "easyocr": 50,
        "paddleocr": 45,
        "rapidocr": 30,
        "tesseract": 10,
    }

    def _prio(method: str) -> int:
        base = method.split(":")[0].replace("image_", "")
        if base.startswith("gemini"):
            return 100
        return priority.get(base, 0)

    def _is_better(score: int, method: str) -> bool:
        if score > best_score:
            return True
        if score < best_score:
            return False
        return _prio(method) > _prio(best_method)

    engines_tried: list[str] = []
    engine_scores: dict[str, int] = {}
    best_text = ""
    best_method = "image_ocr_empty"
    best_rows: list[dict[str, Any]] = []
    best_score = -1

    # 1) Gemini Vision — structured (usually best for handwriting)
    gem_rows, gem_method = extract_rates_gemini_vision(path)
    engines_tried.append("gemini_vision")
    engine_scores["gemini_vision"] = len(gem_rows)
    if gem_rows and _is_better(len(gem_rows), gem_method or "gemini_vision"):
        best_score = len(gem_rows)
        best_rows = gem_rows
        best_method = gem_method or "gemini_vision"
        best_text = "\n".join(
            f"{r.get('product_name')} | {r.get('size') or ''} | {r.get('rate')} | {r.get('gst_pct')}"
            for r in gem_rows
        )

    # Strong Gemini hit → skip slow local engines (still correct, much faster)
    skip_local = best_score >= 5 and _prio(best_method) >= 100

    variants = _prepare_image_variants(path)
    try:
        # 2) Azure Read
        az = _extract_azure_models(path)
        engines_tried.append("azure_read")
        sc, rows = _score_ocr_text(az, parse_fn)
        engine_scores["azure_read"] = sc
        if az and _is_better(sc, "azure_read"):
            best_score = sc
            best_rows = rows
            best_text = az
            best_method = "image_azure_read"

        if not skip_local:
            # EasyOCR/Paddle can OOM Render Starter (512MB) — skip unless explicitly allowed
            allow_heavy = (os.getenv("HOP_ALLOW_HEAVY_OCR") or "").strip().lower() in {
                "1", "true", "yes", "on",
            }
            on_render = (os.getenv("RENDER") or "").strip().lower() in {"true", "1"}
            local_engines: list[tuple[str, Callable[[Path], str]]] = []
            if allow_heavy or not on_render:
                local_engines.extend(
                    [
                        ("easyocr", _extract_easyocr_text),
                        ("paddleocr", _extract_paddle_text),
                    ]
                )
            local_engines.extend(
                [
                    ("rapidocr", _extract_rapid_text),
                    ("tesseract", _extract_tesseract_text),
                ]
            )
            for name, fn in local_engines:
                engines_tried.append(name)
                best_local = ""
                for variant in variants:
                    txt = fn(variant)
                    if len(txt) > len(best_local):
                        best_local = txt
                sc, rows = _score_ocr_text(best_local, parse_fn)
                engine_scores[name] = sc
                if best_local and _is_better(sc, name):
                    best_score = sc
                    best_rows = rows
                    best_text = best_local
                    best_method = f"image_{name}"
        else:
            for name in ("easyocr", "paddleocr", "rapidocr", "tesseract"):
                engine_scores[name] = -1  # skipped
    finally:
        _cleanup_temps(variants, path)

    # If Gemini structured won but text empty, keep structured
    if best_rows and not best_text:
        best_text = "\n".join(str(r.get("product_name")) for r in best_rows)

    return {
        "lines": best_rows,
        "text": best_text,
        "method": best_method if best_text or best_rows else "image_ocr_empty",
        "engines_tried": engines_tried,
        "engine_scores": engine_scores,
    }

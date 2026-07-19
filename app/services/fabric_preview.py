"""Fabric-on-furniture preview for House of Prizm field demos.

Default engine is free local DEMO composite (partner pitches).
Set FABRIC_PREVIEW_ENGINE=gemini + GEMINI_API_KEY later for paid AI.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


MAX_EDGE = 1280


def preview_engine() -> str:
    raw = (os.getenv("FABRIC_PREVIEW_ENGINE") or "demo").strip().lower()
    if raw in {"gemini", "paid", "ai"}:
        return "gemini"
    return "demo"


def _open_rgb(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def _fit(img: Image.Image, max_edge: int = MAX_EDGE) -> Image.Image:
    w, h = img.size
    scale = min(1.0, max_edge / max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    return img


def _tile_fabric(fabric: Image.Image, size: tuple[int, int]) -> Image.Image:
    tw = max(96, size[0] // 3)
    th = max(96, int(tw * fabric.height / max(1, fabric.width)))
    patch = fabric.resize((tw, th), Image.Resampling.LANCZOS)
    patch = ImageEnhance.Color(patch).enhance(1.05)
    canvas = Image.new("RGB", size)
    for y in range(0, size[1], th):
        for x in range(0, size[0], tw):
            canvas.paste(patch, (x, y))
    return canvas


def _center_mask(size: tuple[int, int]) -> Image.Image:
    """Soft ellipse — keeps room edges, applies fabric on furniture-ish center."""
    w, h = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    pad_x, pad_y = int(w * 0.08), int(h * 0.10)
    draw.ellipse((pad_x, pad_y, w - pad_x, h - pad_y), fill=220)
    return mask.filter(ImageFilter.GaussianBlur(radius=max(12, min(w, h) // 25)))


def _watermark(img: Image.Image, text: str) -> Image.Image:
    out = img.copy()
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x, y = 14, out.height - th - 18
    draw.rectangle((x - 8, y - 6, x + tw + 10, y + th + 8), fill=(5, 7, 12))
    draw.text((x, y), text, fill=(37, 224, 255), font=font)
    return out


def demo_apply_fabric(item_bytes: bytes, fabric_bytes: bytes) -> tuple[bytes, dict]:
    """Local free composite suitable for partner demos (not photoreal AI)."""
    sofa = _fit(_open_rgb(item_bytes))
    fabric = _fit(_open_rgb(fabric_bytes), max_edge=640)
    tiled = _tile_fabric(fabric, sofa.size)

    mixed = Image.blend(sofa, tiled, 0.48)
    mixed = ImageEnhance.Contrast(mixed).enhance(1.05)
    mask = _center_mask(sofa.size)
    detail = ImageEnhance.Sharpness(sofa).enhance(1.15)
    base = Image.composite(mixed, detail, mask)
    result = _watermark(base, "PRIZM DEMO · Partner preview")

    buf = io.BytesIO()
    result.save(buf, format="JPEG", quality=88, optimize=True)
    meta = {
        "engine": "demo",
        "mode": "demo",
        "note": "Free local preview for partner demos. Paid AI unlocks when budget is approved.",
        "width": result.width,
        "height": result.height,
    }
    return buf.getvalue(), meta


def gemini_apply_fabric(item_bytes: bytes, fabric_bytes: bytes) -> tuple[bytes, dict]:
    """Paid path placeholder — falls back to demo until Gemini image edit is wired."""
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_GEMINI_API_KEY") or "").strip()
    data, meta = demo_apply_fabric(item_bytes, fabric_bytes)
    meta["engine"] = "demo"
    if not api_key:
        meta["fallback_reason"] = "GEMINI_API_KEY not set"
        meta["note"] = "Paid engine requested but API key missing — showing DEMO render."
    else:
        meta["fallback_reason"] = "gemini_image_edit_pending"
        meta["note"] = (
            "GEMINI key present, but paid image-edit hook is staged. "
            "Showing DEMO composite until budget unlock."
        )
    return data, meta


def apply_fabric(item_bytes: bytes, fabric_bytes: bytes) -> tuple[bytes, dict]:
    if preview_engine() == "gemini":
        return gemini_apply_fabric(item_bytes, fabric_bytes)
    return demo_apply_fabric(item_bytes, fabric_bytes)


def build_demo_swatch(name: str, colors: list[tuple[int, int, int]], size: int = 512) -> bytes:
    """Procedural fabric swatches for catalogue bank when no photos uploaded yet."""
    img = Image.new("RGB", (size, size), colors[0])
    draw = ImageDraw.Draw(img)
    step = 28
    for y in range(0, size, step):
        for x in range(0, size, step):
            c = colors[(x // step + y // step) % len(colors)]
            draw.rectangle((x, y, x + step - 2, y + step - 2), fill=c)
    for i in range(0, size, 4):
        draw.line((0, i, size, i), fill=(255, 255, 255), width=1)
        draw.line((i, 0, i, size), fill=(20, 20, 20), width=1)
    draw.rectangle((0, size - 48, size, size), fill=(5, 7, 12))
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    draw.text((16, size - 34), name, fill=(234, 240, 251), font=font)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


DEMO_FABRIC_BANK = [
    {
        "id": "demo-linen-sand",
        "name": "Linen Sand",
        "category": "Linen",
        "colors": [(210, 190, 160), (198, 178, 148), (220, 205, 180)],
    },
    {
        "id": "demo-velvet-navy",
        "name": "Velvet Navy",
        "category": "Velvet",
        "colors": [(18, 32, 64), (28, 48, 90), (12, 22, 48)],
    },
    {
        "id": "demo-boucle-cream",
        "name": "Bouclé Cream",
        "category": "Bouclé",
        "colors": [(235, 228, 214), (220, 210, 190), (245, 240, 230)],
    },
    {
        "id": "demo-tweed-forest",
        "name": "Tweed Forest",
        "category": "Tweed",
        "colors": [(42, 70, 48), (70, 95, 60), (30, 50, 36), (110, 100, 70)],
    },
]


def list_demo_fabrics() -> list[dict]:
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "category": item["category"],
            "source": "demo_bank",
        }
        for item in DEMO_FABRIC_BANK
    ]


def get_demo_fabric_bytes(fabric_id: str) -> bytes | None:
    for item in DEMO_FABRIC_BANK:
        if item["id"] == fabric_id:
            return build_demo_swatch(item["name"], item["colors"])
    return None


def ensure_preview_dir(base: Path) -> Path:
    target = base / "hop_fabric_previews"
    target.mkdir(parents=True, exist_ok=True)
    return target

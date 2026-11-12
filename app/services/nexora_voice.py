"""Ask Nexora voice: answer text -> natural Hinglish speech (Gemini TTS).

The phone/web send the answer they already show; this turns it into something
pleasant to hear (no markdown, rupees in lakh/crore words, short) and returns
a WAV. Same text + voice is served from a small disk cache, so replaying an
answer costs nothing.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import struct
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_VOICE = "Kore"
# Gemini prebuilt voices offered in the app (name -> label shown to the user).
VOICES: dict[str, str] = {
    "Kore": "Kore — female, clear",
    "Aoede": "Aoede — female, relaxed",
    "Leda": "Leda — female, young",
    "Zephyr": "Zephyr — female, bright",
    "Charon": "Charon — male, calm",
    "Puck": "Puck — male, cheerful",
    "Orus": "Orus — male, firm",
    "Fenrir": "Fenrir — male, energetic",
}
_MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts")
_STYLE = (
    "Read this aloud in a warm, natural, conversational Indian Hinglish voice, "
    "like a helpful colleague giving a quick update. Text: "
)
_MAX_SPOKEN_CHARS = 700
_CACHE_MAX_FILES = 400
_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


class VoiceError(RuntimeError):
    pass


def _indian_amount_words(value: float) -> str:
    v = abs(value)
    if v >= 1_00_00_000:
        num, unit = v / 1_00_00_000, "crore"
    elif v >= 1_00_000:
        num, unit = v / 1_00_000, "lakh"
    elif v >= 1_000:
        num, unit = v / 1_000, "hazaar"
    else:
        return f"{round(v)} rupaye"
    text = f"{num:.2f}".rstrip("0").rstrip(".")
    return f"{text} {unit} rupaye"


def speakable_text(answer: str) -> str:
    """What should be heard: no markdown/tables, Indian money words, short."""
    text = answer or ""
    text = re.sub(r"`{1,3}", "", text)
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or set(line) <= set("-|:= "):
            continue  # blank, '---' rules and table separators
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|") if c.strip()]
            line = ", ".join(cells)
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^[*\-•]\s+", "", line)
        line = re.sub(r"^\d+\.\s+", "", line)
        line = line.replace("**", "").replace("__", "").replace("*", "")
        line = re.sub(r"\s*\|\s*", ", ", line)
        if line and line[-1] not in ".!?:":
            line += "."
        lines.append(line)
    text = " ".join(lines)

    def _money(m: re.Match) -> str:
        try:
            return _indian_amount_words(float(m.group(1).replace(",", "")))
        except ValueError:
            return m.group(0)

    text = re.sub(r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)", _money, text)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    text = re.sub(r"\bpcs\b", "pieces", text, flags=re.I)
    text = re.sub(r"\bSOs\b", "SO", text)
    text = re.sub(r"\bCIs\b", "CI", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > _MAX_SPOKEN_CHARS:
        cut = text[:_MAX_SPOKEN_CHARS]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        text = (cut[: end + 1] if end > 200 else cut).strip() + " Baaki details screen par dekhiye."
    return text


def _wav(pcm: bytes, rate: int = 24000) -> bytes:
    return (
        b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(pcm)) + pcm
    )


def _cache_dir(db_path: str) -> Path:
    d = Path(db_path).resolve().parent / "tts_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _prune(cache: Path) -> None:
    files = sorted(cache.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    for p in files[: max(0, len(files) - _CACHE_MAX_FILES)]:
        try:
            p.unlink()
        except OSError:
            pass


def synthesize(text: str, *, voice: str, api_key: str, db_path: str) -> tuple[bytes, dict]:
    """Return (wav_bytes, info). info: model, cached, chars, tokens."""
    spoken = speakable_text(text)
    if not spoken:
        raise VoiceError("Nothing to say")
    voice = voice if voice in VOICES else DEFAULT_VOICE
    cache = _cache_dir(db_path)
    key = hashlib.sha256(f"{voice}|{spoken}".encode()).hexdigest()[:32]
    hit = cache / f"{key}.wav"
    if hit.exists():
        os.utime(hit)
        return hit.read_bytes(), {"cached": True, "voice": voice, "chars": len(spoken)}
    if not api_key:
        raise VoiceError("GEMINI_API_KEY not configured on the server")

    body = json.dumps({
        "contents": [{"parts": [{"text": _STYLE + spoken}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }).encode()
    last = ""
    for model in _MODELS:
        req = urllib.request.Request(_URL.format(model=model, key=api_key), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
            raw = base64.b64decode(part["data"])
            audio = raw if raw[:4] == b"RIFF" else _wav(raw)
            hit.write_bytes(audio)
            _prune(cache)
            usage = data.get("usageMetadata") or {}
            return audio, {"cached": False, "voice": voice, "model": model, "chars": len(spoken),
                           "prompt_tokens": int(usage.get("promptTokenCount") or 0),
                           "output_tokens": int(usage.get("candidatesTokenCount") or 0)}
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - timeout / bad payload -> next model
            last = str(exc)[:120]
    raise VoiceError(f"Voice failed: {last}")

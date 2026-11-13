"""Ask Nexora voice: speakable text, cache, routes (Gemini call mocked)."""

from __future__ import annotations

import base64
import importlib
import json

from app.services import nexora_voice as nv


def test_speakable_text_reads_well():
    answer = (
        "### 1. Pending SOs\n"
        "* **Total:** 7 SOs (3,012 pcs | ₹12,38,898.46)\n"
        "* **Bed:** ₹62,941.54 baaki\n"
        "---\n"
        "| SO | Value |\n|---|---|\n| 102876640 | Rs 1,50,00,000 |\n"
    )
    out = nv.speakable_text(answer)
    assert "#" not in out and "*" not in out and "|" not in out
    assert "12.39 lakh rupaye" in out
    assert "62.94 hazaar rupaye" in out
    assert "1.5 crore rupaye" in out
    assert "3012 pieces" in out
    assert "7 SO " in out


def test_long_answer_is_cut_with_pointer_to_screen():
    out = nv.speakable_text("Yeh ek line hai. " * 200)
    assert len(out) < 800 and out.endswith("Baaki details screen par dekhiye.")


def _fake_gemini(monkeypatch, calls):
    pcm = b"\x00\x01" * 100

    class Resp:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        calls.append(json.loads(req.data))
        body = {"candidates": [{"content": {"parts": [{"inlineData": {
            "mimeType": "audio/L16;rate=24000", "data": base64.b64encode(pcm).decode()}}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 50}}
        return Resp(json.dumps(body).encode())

    monkeypatch.setattr(nv.urllib.request, "urlopen", fake_urlopen)


def test_synthesize_wraps_pcm_and_caches(tmp_path, monkeypatch):
    calls = []
    _fake_gemini(monkeypatch, calls)
    db = str(tmp_path / "x.sqlite3")
    audio, info = nv.synthesize("Savitri ke 33 CI hain", voice="Charon", api_key="k", db_path=db)
    assert audio[:4] == b"RIFF" and info["voice"] == "Charon" and not info["cached"]
    voice_cfg = calls[0]["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]
    assert voice_cfg["voiceName"] == "Charon"
    again, info2 = nv.synthesize("Savitri ke 33 CI hain", voice="Charon", api_key="k", db_path=db)
    assert again == audio and info2["cached"] and len(calls) == 1
    _, info3 = nv.synthesize("x", voice="NotAVoice", api_key="k", db_path=db)
    assert info3["voice"] == nv.DEFAULT_VOICE


def test_voice_routes_save_choice_and_use_it(tmp_path, monkeypatch):
    db_path = tmp_path / "voice_api.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "voice-test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    app = web_app_module.create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    from centralized_db_system.db import CentralizedDB

    CentralizedDB(str(db_path)).create_user("voice_user", "pass123", role="sales_executive", workspace_id="ws-1")
    login = client.post("/api/v1/auth/login", json={"username": "voice_user", "password": "pass123"})
    h = {"Authorization": f"Bearer {login.get_json()['data']['access_token']}"}

    listed = client.get("/api/v1/ai/voices", headers=h).get_json()["data"]
    assert listed["selected"] == "Kore" and any(v["id"] == "Puck" for v in listed["voices"])
    assert listed["saved"] is False
    assert client.post("/api/v1/ai/voices/select", json={"voice": "Nope"}, headers=h).status_code == 400
    assert client.post("/api/v1/ai/voices/select", json={"voice": "Puck"}, headers=h).status_code == 200
    after = client.get("/api/v1/ai/voices", headers=h).get_json()["data"]
    assert after["selected"] == "Puck" and after["saved"] is True

    calls = []
    _fake_gemini(monkeypatch, calls)
    r = client.post("/api/v1/ai/voice", json={"text": "**33 CI** hain"}, headers=h)
    assert r.status_code == 200 and r.mimetype == "audio/wav" and r.headers["X-Nexora-Voice"] == "Puck"
    r = client.post("/api/v1/ai/voice", json={"text": "sample", "voice": "Leda"}, headers=h)
    assert r.headers["X-Nexora-Voice"] == "Leda"
    assert client.post("/api/v1/ai/voice", json={}, headers=h).status_code == 400
    assert client.post("/api/v1/ai/voice", json={"text": "x"}).status_code == 401

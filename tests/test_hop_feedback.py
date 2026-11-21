"""Customer care feedback (owner 21-11): user → Customer care inbox → reply → the user sees it."""
from __future__ import annotations

import io

from PIL import Image


def _jpg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (60, 40), "navy").save(out, "JPEG")
    return out.getvalue()


def test_feedback_round_trip(tmp_path, monkeypatch):
    from app import hop_feedback
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    sent = []
    monkeypatch.setattr(hop_feedback, "_mail_founder", lambda row, shot: sent.append((row["id"], bool(shot))))
    client, h, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)

    # a normal user (not Customer care) sends a problem with a screenshot
    monkeypatch.setenv("WORKSPACE_OWNER_USERNAME", "someone-else")
    r = client.post("/api/v1/hop/feedback", headers=h, content_type="multipart/form-data",
                    data={"kind": "problem", "text": "Save button does nothing", "screen": "invoices",
                          "app_version": "2.0.1", "file": (io.BytesIO(_jpg()), "s.jpg", "image/jpeg")})
    assert r.status_code == 201, r.get_json()
    fid = r.get_json()["data"]["id"]
    assert sent == [(fid, True)]
    assert client.get("/api/v1/hop/feedback/inbox", headers=h).status_code == 403          # not Customer care
    assert client.post(f"/api/v1/hop/feedback/{fid}/reply", headers=h, json={"reply": "x"}).status_code == 403
    assert client.get(f"/api/v1/hop/feedback/{fid}/shot", headers=h).status_code == 200    # own screenshot
    assert client.post("/api/v1/hop/feedback", headers=h, json={"text": "hi", "kind": "problem"}).status_code == 400

    # the same login as Customer care: sees it in the inbox, replies
    monkeypatch.setenv("WORKSPACE_OWNER_USERNAME", "hopwipe")
    c = client.get("/api/v1/hop/feedback/counts", headers=h).get_json()["data"]
    assert c["is_founder"] and c["new"] == 1
    items = client.get("/api/v1/hop/feedback/inbox", headers=h).get_json()["data"]["items"]
    assert items[0]["text"] == "Save button does nothing" and items[0]["screen"] == "invoices" and items[0]["has_shot"] == 1
    assert client.get("/api/v1/hop/feedback/counts", headers=h).get_json()["data"]["new"] == 0
    r = client.post(f"/api/v1/hop/feedback/{fid}/reply", headers=h, json={"status": "done", "reply": "Fixed in the new update"})
    assert r.status_code == 200 and r.get_json()["data"]["status"] == "done"

    # the user: 1 reply waiting, then reads it
    assert client.get("/api/v1/hop/feedback/counts", headers=h).get_json()["data"]["replies"] == 1
    mine = client.get("/api/v1/hop/feedback", headers=h).get_json()["data"]["items"]
    assert mine[0]["reply"] == "Fixed in the new update"
    assert client.get("/api/v1/hop/feedback/counts", headers=h).get_json()["data"]["replies"] == 0

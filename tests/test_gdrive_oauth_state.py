"""Google Drive OAuth state must be signed — unsigned base64 is forgeable."""

from __future__ import annotations

import base64
import json
import os

import pytest

os.environ.setdefault("SECRET_KEY", "oauth-state-test-secret-key-32bytes!!")

from app.storage.oauth import GoogleDriveOAuth


@pytest.fixture(autouse=True)
def _oauth_secret(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "oauth-state-test-secret-key-32bytes!!")


def test_oauth_state_round_trip_preserves_user_and_workspace():
    state = GoogleDriveOAuth.encode_oauth_state(
        {"user_id": 42, "workspace_id": "ws-a"}
    )
    parsed = GoogleDriveOAuth.parse_oauth_state(state)
    assert parsed["user_id"] == 42
    assert parsed["workspace_id"] == "ws-a"


def test_forged_unsigned_base64_state_is_rejected():
    # Classic attack: attacker encodes victim ids with no signature.
    forged = base64.urlsafe_b64encode(
        json.dumps({"user_id": 1, "workspace_id": "victim"}).encode()
    ).decode()
    assert GoogleDriveOAuth.parse_oauth_state(forged) == {}

    legacy = base64.b64encode(b"1:victim-ws:12345").decode()
    assert GoogleDriveOAuth.parse_oauth_state(legacy) == {}


def test_tampered_signed_state_is_rejected():
    state = GoogleDriveOAuth.encode_oauth_state(
        {"user_id": 7, "workspace_id": "default"}
    )
    # Flip a character in the middle of the token
    chars = list(state)
    mid = len(chars) // 2
    chars[mid] = "A" if chars[mid] != "A" else "B"
    tampered = "".join(chars)
    assert GoogleDriveOAuth.parse_oauth_state(tampered) == {}


def test_state_signed_with_different_secret_is_rejected(monkeypatch):
    state = GoogleDriveOAuth.encode_oauth_state(
        {"user_id": 9, "workspace_id": "default"}
    )
    monkeypatch.setenv("SECRET_KEY", "some-other-secret-key-value!!!!!!")
    assert GoogleDriveOAuth.parse_oauth_state(state) == {}

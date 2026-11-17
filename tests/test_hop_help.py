"""HoP in-app help: every menu screen has a guide; search works for Ask Nexora."""
from __future__ import annotations

from app import hop_help


def test_help_topics_and_search():
    t = hop_help.topic("sale_invoices")
    assert t["key"] == "invoices" and t["steps"] and "Fill with AI" in t["ai"]
    assert hop_help.search("invoice kaise banau")[0]["key"] == "invoices"
    assert hop_help.search("godown stock move")[0]["key"] == "godowns"
    for k, v in hop_help.HELP.items():
        assert v["title"] and v["what"] and isinstance(v["steps"], list), k


def test_help_route(tmp_path, monkeypatch):
    from tests.test_hop_wipe_password import _setup_hop_wipe_client

    client, h, _ = _setup_hop_wipe_client(tmp_path, monkeypatch)
    assert client.get("/api/v1/hop/help?screen=godowns", headers=h).get_json()["data"]["title"] == "Godowns"
    assert "invoices" in client.get("/api/v1/hop/help", headers=h).get_json()["data"]["topics"]

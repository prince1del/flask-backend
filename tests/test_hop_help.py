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


def test_every_screen_has_on_this_screen():
    from app.hop_help import HELP, topic

    for k in HELP:
        t = topic(k)
        assert t["shows"], f"{k}: no 'On this screen' lines"
        assert all(" — " in x for x in t["shows"]), k


def test_every_phone_menu_screen_has_help():
    # keys of HopShellDrawer (phone menu) + web views — Info must never say "No guide"
    from app.hop_help import topic

    keys = ["dashboard", "parties", "invoices", "products", "samples", "fabric_preview", "vendor_cmp", "orders",
            "dispatches", "complaints", "customer_dash", "daily", "profit", "targets", "theme", "team_duty",
            "vyapar_import", "wipe_data", "reminders", "leads", "meetings", "projects", "vendors", "customers",
            "feedback"]
    for k in keys:
        assert topic(k) and topic(k)["shows"], k

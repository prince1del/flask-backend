"""Ask Nexora data tools: Distributor Hisaab and Pending SO trimming/filters."""

from __future__ import annotations

from app.services.nexora_ai_agent import (
    _name_filter,
    agent_gemini_models,
    hisaab_for_ai,
    pending_so_for_ai,
)


def _ci(no, status, qty=10, gross=1000.0):
    return {"invoice_no": no, "invoice_date": "2026-10-01", "so_number": "S1", "qty": qty,
            "gross": gross, "net": gross * 0.9, "covered": 0, "status": status, "taxable": gross}


LEDGER = {
    "as_of": "2026-11-12",
    "distributors": [
        {
            "distributor_name": "Alpha Steel Traders",
            "totals": {"payment_due": 500},
            "categories": [
                {"season": "AW26", "category": "Bed", "cis": [_ci("C1", "paid"), _ci("C2", "unpaid")],
                 "pending_sos": [{"so_number": "S9", "status": "open", "pending_qty": 5, "pending_net": 700}],
                 "order_payable": 3000, "received": 1000, "billed": 1800, "payment_due": 2000,
                 "unbilled_advance": -800, "due_date": "2026-11-01", "cd_percent": 3, "tds_percent": 0},
                {"season": "AW26", "category": "Bath", "cis": [_ci("C3", "part_paid", qty=4)],
                 "pending_sos": [], "order_payable": 900, "received": 900, "billed": 900,
                 "payment_due": 0, "unbilled_advance": 0},
            ],
            "unlinked_cis": [],
            "alerts": [{"level": "alert", "text": "Bed AW26: overdue"}],
        },
        {"distributor_name": "Beta Homes", "totals": {}, "categories": [], "unlinked_cis": [], "alerts": []},
    ],
}


def test_hisaab_splits_ci_by_category_and_status():
    out = hisaab_for_ai(LEDGER, distributor_name="alpha")
    assert out["count"] == 1
    cats = {c["category"]: c for c in out["distributors"][0]["categories"]}
    assert cats["Bed"]["ci_count"] == 2
    assert cats["Bed"]["ci_count_by_status"] == {"paid": 1, "unpaid": 1}
    assert cats["Bed"]["pending_so_count"] == 1 and cats["Bed"]["pending_so_value"] == 700
    assert cats["Bath"]["ci_count"] == 1 and cats["Bath"]["ci_qty_total"] == 4
    assert out["distributors"][0]["alerts"] == ["Bed AW26: overdue"]


def test_hisaab_category_filter_and_typo_name():
    out = hisaab_for_ai(LEDGER, distributor_name="Alpha Steal", category="bath")
    assert [c["category"] for c in out["distributors"][0]["categories"]] == ["Bath"]


def test_hisaab_unknown_distributor_says_so():
    out = hisaab_for_ai(LEDGER, distributor_name="Zeta")
    assert "error" in out and "Alpha Steel Traders" in out["distributors_on_file"]


def test_hisaab_no_filter_lists_all():
    assert hisaab_for_ai(LEDGER)["count"] == 2


def test_pending_so_totals_per_category():
    items = [
        {"so_number": "1", "distributor_name": "Alpha Steel Traders", "season": "AW26", "category": "Bed",
         "status": "open", "pending_qty": 5, "pending_net": 100},
        {"so_number": "2", "distributor_name": "Alpha Steel Traders", "season": "AW26", "category": "Bed",
         "status": "partial", "pending_qty": 3, "pending_net": 50},
        {"so_number": "3", "distributor_name": "Beta Homes", "season": "AW26", "category": "Bath",
         "status": "open", "pending_qty": 1, "pending_net": 10},
    ]
    out = pending_so_for_ai(items, distributor_name="alpha")
    assert out["count"] == 2
    assert out["by_distributor_season_category"]["Alpha Steel Traders | AW26 | Bed"] == {
        "so_count": 2, "pending_qty": 8.0, "pending_value": 150.0}
    assert pending_so_for_ai(items, category="Bath")["count"] == 1
    assert pending_so_for_ai(items, distributor_name="nobody")["count"] == 0


def test_name_filter():
    names = ["Alpha Steel Traders", "Beta Homes"]
    assert _name_filter(names, "") is None
    assert _name_filter(names, "beta") == {"Beta Homes"}
    assert _name_filter(names, "alfa steel") == {"Alpha Steel Traders"}


def test_agent_models_fast_first(monkeypatch):
    monkeypatch.delenv("GEMINI_AGENT_MODELS", raising=False)
    monkeypatch.setenv("GEMINI_FLASH_MODEL", "gemini-3.5-flash")
    assert agent_gemini_models() == ("gemini-3.6-flash", "gemini-3.5-flash")
    monkeypatch.setenv("GEMINI_AGENT_MODELS", "m1,gemini-3.5-flash")
    assert agent_gemini_models() == ("m1", "gemini-3.5-flash")


def test_phone_ask_sends_counting_questions_to_ai():
    from app.routes.data import _looks_like_ai_data_question as q

    assert q("Savitri Steel ki kitne CI pending hai, uske kitne Bed or kitne Bath hai?")
    assert q("Bernina ke kitne SO pending hai?")
    assert q("how much payment is outstanding for Kalra")
    assert not q("Savitri Steel")
    assert not q("Kalra ka number kya hai")
    assert not q("aster ka mrp kya hai")


def test_follow_up_memory(tmp_path, monkeypatch):
    from app.services import nexora_ai_agent as a

    monkeypatch.setattr(a, "_db_path", lambda: str(tmp_path / "turns.sqlite3"))
    assert a.recent_turns(7, "ws") == []
    assert not a.is_follow_up("aur Bath ke?", [])

    a.record_turn(7, "ws", "Savitri ke kitne CI hai?", "33 CI: Bed 31, Bath 2")
    a.record_turn(8, "ws", "other user", "x")
    turns = a.recent_turns(7, "ws")
    assert turns == [{"question": "Savitri ke kitne CI hai?", "answer": "33 CI: Bed 31, Bath 2"}]

    assert a.is_follow_up("aur Bath ke?", turns)
    assert a.is_follow_up("uska payment kitna baaki hai abhi tak bataiye", turns)
    assert not a.is_follow_up("Bernina International ke saare sales order dikhao season AW26", turns)

    hist = a._history_contents(turns)
    assert [h["role"] for h in hist] == ["user", "model"]
    assert hist[1]["parts"][0]["text"].startswith("33 CI")


def test_ask_sends_history_before_question(monkeypatch):
    from app.services import nexora_ai_agent as a

    seen = {}

    def fake_call(contents):
        seen["contents"] = contents
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}, "m"

    monkeypatch.setattr(a, "_call_gemini", fake_call)
    monkeypatch.setattr(a, "_log_usage", lambda **k: None)
    out = a.ask_order_desk("aur Bath ke?", user_id=1, workspace_id="ws",
                           history=[{"question": "Savitri ke CI?", "answer": "Bed 31"}])
    assert out == "ok"
    texts = [c["parts"][0]["text"] for c in seen["contents"]]
    assert texts == ["Savitri ke CI?", "Bed 31", "aur Bath ke?"]

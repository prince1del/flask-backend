"""IFSC → bank / branch / address (no network: the directory call is replaced)."""

from app.services import ifsc_lookup as m


def test_bad_format_needs_no_call(monkeypatch):
    monkeypatch.setattr(m, "_fetch", lambda i: (_ for _ in ()).throw(AssertionError("no call")))
    out = m.lookup_ifsc("sbin12")
    assert out["ok"] is False and out["valid_format"] is False


def test_found_fills_bank_branch_address(monkeypatch):
    m._CACHE.clear()
    monkeypatch.setattr(m, "_fetch", lambda i: {"BANK": "Test Bank", "BRANCH": "MAIN", "ADDRESS": "1 Road",
                                                 "CITY": "Pune", "STATE": "Maharashtra"})
    out = m.lookup_ifsc(" tstb0000001 ")
    assert out["ok"] and out["ifsc"] == "TSTB0000001"
    assert out["bank_name"] == "Test Bank"
    assert out["branch_with_address"] == "MAIN, 1 Road, Pune, Maharashtra"


def test_not_found_and_offline(monkeypatch):
    m._CACHE.clear()
    monkeypatch.setattr(m, "_fetch", lambda i: {})
    assert "not found" in m.lookup_ifsc("TSTB0000002")["message"]
    monkeypatch.setattr(m, "_fetch", lambda i: None)
    assert m.lookup_ifsc("TSTB0000003")["ok"] is False

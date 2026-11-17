"""Executive UI brand: the workspace's own company, never a fixed brand; Bombay Dyeing workspaces unchanged."""
from __future__ import annotations

from app.routes import auth


class _NoDb:
    db_path = ":memory:"

    def get_company_id_for_workspace(self, ws):
        raise RuntimeError("no companies table")


def test_bombay_dyeing_workspace_keeps_exact_brand():
    assert auth.workspace_brand(_NoDb(), "bombay_dyeing_gt_north")["brand"] == "Bombay Dyeing"


def test_other_company_gets_its_own_name(monkeypatch):
    from app.services import company_invites

    monkeypatch.setattr(company_invites, "company_name_for_workspace", lambda db, ws: "Sharma Textiles")
    out = auth.workspace_brand(_NoDb(), "sharma_textiles")
    assert out == {"brand": "Sharma Textiles", "company_name": "Sharma Textiles"}


def test_no_company_name_is_blank_not_another_brand():
    out = auth.workspace_brand(_NoDb(), "new_shop")
    assert out["brand"] == "" and "Bombay" not in out["company_name"]

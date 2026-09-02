"""Tests for unified Gmail CI import (thin transport → _ingest_one_ci_pdf)."""

from __future__ import annotations

from app.services import mail_sync_diagnostics as diag


def test_mail_sync_assess_not_connected():
    result = diag.assess({"connected": False})
    assert result["outcome"] == "mail_sync_not_connected"


def test_mail_sync_assess_ci_imported():
    result = diag.assess({"connected": True, "ci_imported": 2, "messages_matched": 3})
    assert result["outcome"] == "mail_sync_imported"


def test_mail_sync_assess_needs_review():
    result = diag.assess(
        {"connected": True, "ci_imported": 0, "pending_review": 1, "messages_matched": 1, "scanned": 1}
    )
    assert result["outcome"] == "mail_sync_needs_review"


def test_ingest_one_ci_pdf_includes_preview_on_review(monkeypatch):
    from app.routes import data as data_routes

    preview = {
        "invoice_no": "1400010999",
        "order_ref_no": "102899999",
        "no_match_found": True,
        "buyer_name": "Test Distributor",
    }

    def fake_upload(**_kwargs):
        class Resp:
            def __init__(self):
                self._json = {"success": True, "data": preview}

            def get_json(self, silent=True):
                return self._json

        return Resp()

    def fake_auto_confirm(p):
        return {
            "state": "review",
            "status": "Needs review — pick distributor",
            "invoice_no": p.get("invoice_no"),
            "order_ref_no": p.get("order_ref_no"),
            "tracking_id": None,
        }

    monkeypatch.setattr(data_routes, "_upload_invoice_v2_impl", fake_upload)
    monkeypatch.setattr(data_routes, "_auto_confirm_ci_preview", fake_auto_confirm)
    monkeypatch.setattr(
        data_routes,
        "_flask_response_payload",
        lambda resp: (200, resp.get_json()),
    )

    row = data_routes._ingest_one_ci_pdf("ci.pdf", b"%PDF-1.4 test")
    assert row["state"] == "review"
    assert row.get("preview") == preview

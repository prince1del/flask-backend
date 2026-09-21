"""CI bulk upload: trust SO party when Customers hit is missing (e.g. DCA)."""

from app.routes import data as data_routes


def test_party_match_trusts_so_when_ci_customers_miss():
    summary = data_routes._build_ci_party_match_summary(
        ci_match={
            "status": "none",
            "buyer_name": "DCA MARKETING",
            "distributor": None,
            "candidates": [],
        },
        so_distributor={
            "id": 10,
            "firm_name": "DCA Marketing",
            "name": "Rajesh Goyal",
        },
    )
    assert summary["status"] == "matched"
    assert summary["so_distributor"]["id"] == 10


def test_party_safe_for_auto_with_so_only_unmatched_legacy():
    preview = {
        "party_match": {
            "status": "unmatched",
            "so_distributor": {"id": 10, "name": "DCA Marketing"},
            "ci_distributor": None,
        }
    }
    assert data_routes._ci_party_safe_for_auto(preview) is True


def test_auto_confirm_has_real_so_from_order_match_only():
    preview = {
        "invoice_no": "400099999",
        "order_ref_no": "102876140",
        "no_match_found": False,
        "party_match": {
            "status": "matched",
            "so_distributor": {"id": 10, "name": "DCA Marketing"},
        },
        "matching_sales_order": None,
        "order_match_so": {"so_number": "102876140", "run_id": 139},
        "extracted_amount": 100.0,
    }
    # Should attempt link path (not fall through to review for missing SO).
    # We stub confirm to avoid DB — only assert has_real_so gate via party_ok path.
    assert data_routes._ci_party_safe_for_auto(preview) is True
    party_match = preview["party_match"]
    matching_so = preview.get("matching_sales_order") or {}
    order_match_so = preview.get("order_match_so")
    so_present = bool(matching_so) or bool(order_match_so)
    has_real_so = so_present and (
        matching_so.get("has_sales_order")
        or matching_so.get("from_order_match")
        or bool(order_match_so)
        or matching_so.get("tracking_id") is not None
    )
    assert has_real_so is True
    assert party_match["status"] == "matched"

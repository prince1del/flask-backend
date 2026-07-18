from flask import Blueprint, jsonify, request
from sqlalchemy import func

from app.db import db
from app.models import FinanceAccount, GSTReturn, VATReturn, Invoice, InvoicePayment
from app.routes.auth import require_jwt_auth, get_workspace_id

finance_bp = Blueprint("finance", __name__, url_prefix="/api/v1/finance")


def _account_to_dict(account: FinanceAccount) -> dict:
    return {
        "id": account.id,
        "name": account.name,
        "account_type": account.account_type,
        "opening_balance": account.opening_balance,
        "notes": account.notes,
        "created_at": account.created_at.isoformat() if account.created_at else None,
    }


def _gst_to_dict(item: GSTReturn) -> dict:
    return {
        "id": item.id,
        "period": item.period,
        "sales_amount": item.sales_amount,
        "purchase_amount": item.purchase_amount,
        "tax_rate": item.tax_rate,
        "tax_amount": item.tax_amount,
        "filed_status": item.filed_status,
        "notes": item.notes,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _vat_to_dict(item: VATReturn) -> dict:
    return {
        "id": item.id,
        "period": item.period,
        "sales_amount": item.sales_amount,
        "purchase_amount": item.purchase_amount,
        "tax_rate": item.tax_rate,
        "tax_amount": item.tax_amount,
        "filed_status": item.filed_status,
        "notes": item.notes,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


@finance_bp.route("/accounts", methods=["GET"])
@require_jwt_auth
def list_accounts():
    workspace_id = get_workspace_id()
    accounts = FinanceAccount.query.filter_by(workspace_id=workspace_id).order_by(FinanceAccount.created_at.desc()).all()
    return jsonify({"success": True, "data": [_account_to_dict(account) for account in accounts]}), 200


@finance_bp.route("/accounts", methods=["POST"])
@require_jwt_auth
def create_account():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    account_type = (payload.get("account_type") or "asset").strip() or "asset"
    opening_balance = float(payload.get("opening_balance") or 0.0)
    notes = (payload.get("notes") or "").strip()

    if not name:
        return jsonify({"success": False, "error": {"message": "Account name is required."}}), 400

    workspace_id = get_workspace_id()
    account = FinanceAccount(
        name=name,
        account_type=account_type,
        opening_balance=opening_balance,
        notes=notes,
        workspace_id=workspace_id,
    )
    db.session.add(account)
    db.session.commit()
    return jsonify({"success": True, "data": _account_to_dict(account)}), 200


@finance_bp.route("/gst", methods=["GET"])
@require_jwt_auth
def list_gst_returns():
    workspace_id = get_workspace_id()
    items = GSTReturn.query.filter_by(workspace_id=workspace_id).order_by(GSTReturn.created_at.desc()).all()
    return jsonify({"success": True, "data": [_gst_to_dict(item) for item in items]}), 200


@finance_bp.route("/gst", methods=["POST"])
@require_jwt_auth
def create_gst_return():
    payload = request.get_json(silent=True) or {}
    period = (payload.get("period") or "").strip()
    sales_amount = float(payload.get("sales_amount") or 0.0)
    purchase_amount = float(payload.get("purchase_amount") or 0.0)
    tax_rate = float(payload.get("tax_rate") or 0.0)
    filed_status = (payload.get("filed_status") or "draft").strip() or "draft"
    notes = (payload.get("notes") or "").strip()

    if not period:
        return jsonify({"success": False, "error": {"message": "Period is required."}}), 400

    workspace_id = get_workspace_id()
    item = GSTReturn(
        period=period,
        sales_amount=sales_amount,
        purchase_amount=purchase_amount,
        tax_rate=tax_rate,
        tax_amount=(sales_amount - purchase_amount) * (tax_rate / 100.0),
        filed_status=filed_status,
        notes=notes,
        workspace_id=workspace_id,
    )
    db.session.add(item)
    db.session.commit()
    return jsonify({"success": True, "data": _gst_to_dict(item)}), 200


@finance_bp.route("/vat", methods=["GET"])
@require_jwt_auth
def list_vat_returns():
    workspace_id = get_workspace_id()
    items = VATReturn.query.filter_by(workspace_id=workspace_id).order_by(VATReturn.created_at.desc()).all()
    return jsonify({"success": True, "data": [_vat_to_dict(item) for item in items]}), 200


@finance_bp.route("/vat", methods=["POST"])
@require_jwt_auth
def create_vat_return():
    payload = request.get_json(silent=True) or {}
    period = (payload.get("period") or "").strip()
    sales_amount = float(payload.get("sales_amount") or 0.0)
    purchase_amount = float(payload.get("purchase_amount") or 0.0)
    tax_rate = float(payload.get("tax_rate") or 0.0)
    filed_status = (payload.get("filed_status") or "draft").strip() or "draft"
    notes = (payload.get("notes") or "").strip()

    if not period:
        return jsonify({"success": False, "error": {"message": "Period is required."}}), 400

    workspace_id = get_workspace_id()
    item = VATReturn(
        period=period,
        sales_amount=sales_amount,
        purchase_amount=purchase_amount,
        tax_rate=tax_rate,
        tax_amount=(sales_amount - purchase_amount) * (tax_rate / 100.0),
        filed_status=filed_status,
        notes=notes,
        workspace_id=workspace_id,
    )
    db.session.add(item)
    db.session.commit()
    return jsonify({"success": True, "data": _vat_to_dict(item)}), 200


@finance_bp.route("/summary", methods=["GET"])
@require_jwt_auth
def finance_summary():
    workspace_id = get_workspace_id()
    accounts = FinanceAccount.query.filter_by(workspace_id=workspace_id).count()
    gst_items = GSTReturn.query.filter_by(workspace_id=workspace_id).all()
    vat_items = VATReturn.query.filter_by(workspace_id=workspace_id).all()
    invoices = Invoice.query.filter_by(workspace_id=workspace_id).all()
    payments = InvoicePayment.query.join(Invoice).filter(Invoice.workspace_id == workspace_id).all()
    return jsonify(
        {
            "success": True,
            "data": {
                "accounts": accounts,
                "gst_returns": len(gst_items),
                "vat_returns": len(vat_items),
                "gst_tax_due": round(sum(item.tax_amount for item in gst_items), 2),
                "vat_tax_due": round(sum(item.tax_amount for item in vat_items), 2),
                "invoices": len(invoices),
                "payments": len(payments),
                "revenue": round(sum(item.net_amount for item in invoices), 2),
                "collected": round(sum(item.amount_paid for item in payments), 2),
            },
        }
    ), 200


@finance_bp.route("/ledger", methods=["GET"])
@require_jwt_auth
def ledger_report():
    workspace_id = get_workspace_id()
    account_id = request.args.get("account_id", type=int)
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    query = Invoice.query.filter_by(workspace_id=workspace_id)
    if account_id:
        query = query.filter(Invoice.id == account_id)
    if start_date:
        query = query.filter(Invoice.invoice_date >= start_date)
    if end_date:
        query = query.filter(Invoice.invoice_date <= end_date)

    results = query.order_by(Invoice.invoice_date.desc()).limit(50).all()
    return jsonify(
        {
            "success": True,
            "data": {
                "items": [
                    {
                        "id": item.id,
                        "date": item.invoice_date.isoformat() if item.invoice_date else None,
                        "reference": item.invoice_number,
                        "debit": round(item.total_amount, 2),
                        "credit": round(item.paid_amount, 2),
                        "balance": round(item.net_amount - item.paid_amount, 2),
                        "status": item.payment_status,
                    }
                    for item in results
                ]
            },
        }
    ), 200


@finance_bp.route("/trial-balance", methods=["GET"])
@require_jwt_auth
def trial_balance():
    workspace_id = get_workspace_id()
    accounts = FinanceAccount.query.filter_by(workspace_id=workspace_id).order_by(FinanceAccount.name).all()
    rows = []
    for account in accounts:
        opening = account.opening_balance or 0.0
        rows.append(
            {
                "account_id": account.id,
                "account_name": account.name,
                "account_type": account.account_type,
                "debit": round(opening if opening >= 0 else 0.0, 2),
                "credit": round(-opening if opening < 0 else 0.0, 2),
                "balance": round(opening, 2),
            }
        )
    return jsonify({"success": True, "data": {"accounts": rows}}), 200


@finance_bp.route("/financial-statements", methods=["GET"])
@require_jwt_auth
def financial_statements():
    workspace_id = get_workspace_id()
    invoices = Invoice.query.filter_by(workspace_id=workspace_id).all()
    revenue = sum(item.net_amount for item in invoices)
    tax = sum(item.tax_amount for item in invoices)
    net_profit = revenue - tax

    accounts = FinanceAccount.query.filter_by(workspace_id=workspace_id).all()
    assets = sum(account.opening_balance for account in accounts if account.account_type == "asset")
    liabilities = sum(account.opening_balance for account in accounts if account.account_type == "liability")
    equity = assets - liabilities

    return jsonify(
        {
            "success": True,
            "data": {
                "profit_and_loss": {
                    "revenue": round(revenue, 2),
                    "tax": round(tax, 2),
                    "net_profit": round(net_profit, 2),
                },
                "balance_sheet": {
                    "assets": round(assets, 2),
                    "liabilities": round(liabilities, 2),
                    "equity": round(equity, 2),
                },
            },
        }
    ), 200

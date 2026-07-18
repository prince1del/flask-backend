from flask import Blueprint, request, jsonify
from sqlalchemy import func
from datetime import datetime, date, timezone

from app.db import db
from app.models import SalesOrder, SalesOrderItem, Invoice, InvoicePayment, Dispatch, Distributor, Retailer, GSTReturn, VATReturn
from app.routes.auth import get_workspace_id, require_jwt_auth

sales_bp = Blueprint('sales', __name__, url_prefix='/api/v1')


def _current_user():
    return getattr(request, 'user', None)


def _get_workspace_id():
    return get_workspace_id()


def _generate_so_number(workspace_id):
    year = datetime.now(timezone.utc).year
    last = SalesOrder.query.filter(
        SalesOrder.so_number.like(f'SO-{year}-%'),
    ).order_by(SalesOrder.id.desc()).first()
    counter = 1 if not last else int(last.so_number.rsplit('-', 1)[-1]) + 1
    return f'SO-{year}-{counter:04d}'


def _generate_invoice_number(workspace_id):
    year = datetime.now(timezone.utc).year
    last = Invoice.query.filter(
        Invoice.invoice_number.like(f'INV-{year}-%'),
    ).order_by(Invoice.id.desc()).first()
    counter = 1 if not last else int(last.invoice_number.rsplit('-', 1)[-1]) + 1
    return f'INV-{year}-{counter:04d}'


def _recalculate_sales_order_totals(so, items=None, tax_rate=None):
    if items is None:
        items = so.items

    total_amount = 0.0
    for item in items:
        if isinstance(item, dict):
            quantity = float(item.get('quantity', 0))
            unit_price = float(item.get('unit_price', 0))
        else:
            quantity = float(getattr(item, 'quantity', 0))
            unit_price = float(getattr(item, 'unit_price', 0))
        total_amount += quantity * unit_price

    if tax_rate is None:
        if so.total_amount and so.tax_amount:
            tax_rate = (so.tax_amount / so.total_amount) * 100.0
        else:
            tax_rate = 0.0
    else:
        tax_rate = float(tax_rate)

    so.total_amount = total_amount
    so.tax_amount = total_amount * (tax_rate / 100.0)
    so.net_amount = so.total_amount + so.tax_amount


def _auto_post_invoice_tax(invoice, workspace_id):
    period = invoice.invoice_date.strftime('%Y-%m') if invoice.invoice_date else date.today().strftime('%Y-%m')
    tax_rate = 0.0
    if invoice.total_amount:
        tax_rate = (invoice.tax_amount / invoice.total_amount) * 100.0

    for model in (GSTReturn, VATReturn):
        existing = model.query.filter_by(period=period, workspace_id=workspace_id).first()
        if existing:
            existing.sales_amount += float(invoice.total_amount or 0.0)
            existing.tax_amount += float(invoice.tax_amount or 0.0)
            existing.tax_rate = tax_rate
            existing.notes = f"Auto-posted from invoice {invoice.invoice_number}"
        else:
            db.session.add(
                model(
                    period=period,
                    sales_amount=float(invoice.total_amount or 0.0),
                    purchase_amount=0.0,
                    tax_rate=tax_rate,
                    tax_amount=float(invoice.tax_amount or 0.0),
                    filed_status='draft',
                    notes=f"Auto-posted from invoice {invoice.invoice_number}",
                    workspace_id=workspace_id,
                )
            )


# ========== SALES ORDER ENDPOINTS ==========

@sales_bp.route('/sales-orders', methods=['GET'])
@require_jwt_auth
def get_sales_orders():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    offset = (page - 1) * limit
    status = request.args.get('status')
    distributor_id = request.args.get('distributor_id', type=int)
    retailer_id = request.args.get('retailer_id', type=int)
    workspace_id = _get_workspace_id()

    query = SalesOrder.query.filter_by(workspace_id=workspace_id)
    if status:
        query = query.filter_by(status=status)
    if distributor_id:
        query = query.filter_by(distributor_id=distributor_id)
    if retailer_id:
        query = query.filter_by(retailer_id=retailer_id)

    total = query.count()
    orders = query.order_by(SalesOrder.created_at.desc()).offset(offset).limit(limit).all()

    return jsonify({'success': True, 'data': {'count': total, 'page': page, 'limit': limit, 'results': [o.to_dict(include_items=False) for o in orders]}, 'message': 'Sales orders retrieved successfully'}), 200


@sales_bp.route('/sales-orders/<int:id>', methods=['GET'])
@require_jwt_auth
def get_sales_order(id):
    workspace_id = _get_workspace_id()
    so = SalesOrder.query.filter_by(id=id, workspace_id=workspace_id).first()
    if not so:
        return jsonify({'success': False, 'data': None, 'message': 'Sales order not found'}), 404
    return jsonify({'success': True, 'data': so.to_dict(include_items=True), 'message': 'Sales order retrieved successfully'}), 200


@sales_bp.route('/sales-orders', methods=['POST'])
@require_jwt_auth
def create_sales_order():
    data = request.get_json(silent=True) or {}
    distributor_id = data.get('distributor_id')
    retailer_id = data.get('retailer_id')
    items = data.get('items') or []

    if not distributor_id:
        return jsonify({'success': False, 'data': None, 'message': 'Distributor ID is required'}), 400
    if not retailer_id:
        return jsonify({'success': False, 'data': None, 'message': 'Retailer ID is required'}), 400
    if not items:
        return jsonify({'success': False, 'data': None, 'message': 'At least one sales order item is required'}), 400

    workspace_id = _get_workspace_id()
    distributor = Distributor.query.filter_by(id=distributor_id, workspace_id=workspace_id).first()
    if not distributor:
        return jsonify({'success': False, 'data': None, 'message': 'Distributor not found'}), 404
    retailer = Retailer.query.filter_by(id=retailer_id, workspace_id=workspace_id).first()
    if not retailer:
        return jsonify({'success': False, 'data': None, 'message': 'Retailer not found'}), 404

    try:
        tax_rate = float(data.get('tax_rate', 0.0))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'data': None, 'message': 'Invalid tax rate value'}), 400

    so = SalesOrder(
        so_number=_generate_so_number(workspace_id),
        distributor_id=distributor_id,
        retailer_id=retailer_id,
        order_date=datetime.strptime(data.get('order_date', date.today().isoformat()), '%Y-%m-%d').date(),
        so_date=datetime.strptime(data.get('so_date', date.today().isoformat()), '%Y-%m-%d').date(),
        status='draft',
        workspace_id=workspace_id,
        created_by=_current_user().get('user_id') if _current_user() else None,
    )

    total_amount = 0.0
    for item in items:
        quantity = float(item.get('quantity', 0))
        unit_price = float(item.get('unit_price', 0))
        line_total = quantity * unit_price
        total_amount += line_total
        so.items.append(SalesOrderItem(product_code=item.get('product_code'), product_name=item.get('product_name'), quantity=quantity, unit_price=unit_price, line_total=line_total))

    if 'tax_rate' in data:
        try:
            tax_rate = float(data.get('tax_rate'))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'data': None, 'message': 'Invalid tax rate value'}), 400
        _recalculate_sales_order_totals(so, items=items, tax_rate=tax_rate)
    elif 'items' in data:
        _recalculate_sales_order_totals(so, items=items)

    try:
        db.session.add(so)
        db.session.commit()
        return jsonify({'success': True, 'data': so.to_dict(include_items=True), 'message': 'Sales order created successfully'}), 201
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error creating sales order: {exc}'}), 500


@sales_bp.route('/sales-orders/<int:id>', methods=['PUT'])
@require_jwt_auth
def update_sales_order(id):
    workspace_id = _get_workspace_id()
    so = SalesOrder.query.filter_by(id=id, workspace_id=workspace_id).first()
    if not so:
        return jsonify({'success': False, 'data': None, 'message': 'Sales order not found'}), 404
    if so.status != 'draft':
        return jsonify({'success': False, 'data': None, 'message': 'Only draft sales orders can be edited'}), 400

    data = request.get_json(silent=True) or {}
    if 'order_date' in data:
        so.order_date = datetime.strptime(data.get('order_date'), '%Y-%m-%d').date()
    if 'so_date' in data:
        so.so_date = datetime.strptime(data.get('so_date'), '%Y-%m-%d').date()
    if 'distributor_id' in data:
        distributor = Distributor.query.filter_by(id=data.get('distributor_id'), workspace_id=workspace_id).first()
        if not distributor:
            return jsonify({'success': False, 'data': None, 'message': 'Distributor not found'}), 404
        so.distributor_id = data.get('distributor_id')
    if 'retailer_id' in data:
        retailer = Retailer.query.filter_by(id=data.get('retailer_id'), workspace_id=workspace_id).first()
        if not retailer:
            return jsonify({'success': False, 'data': None, 'message': 'Retailer not found'}), 404
        so.retailer_id = data.get('retailer_id')

    if 'items' in data:
        SalesOrderItem.query.filter_by(so_id=id).delete()
        for item in data['items']:
            quantity = float(item.get('quantity', 0))
            unit_price = float(item.get('unit_price', 0))
            line_total = quantity * unit_price
            db.session.add(SalesOrderItem(so_id=id, product_code=item.get('product_code'), product_name=item.get('product_name'), quantity=quantity, unit_price=unit_price, line_total=line_total))

    update_items = data.get('items')
    if 'items' in data or 'tax_rate' in data:
        items_for_totals = []
        if update_items is not None:
            items_for_totals = update_items
        else:
            items_for_totals = so.items
        tax_rate = data.get('tax_rate')
        _recalculate_sales_order_totals(so, items=items_for_totals, tax_rate=tax_rate)

    so.updated_at = datetime.now(timezone.utc)
    try:
        db.session.commit()
        return jsonify({'success': True, 'data': so.to_dict(include_items=True), 'message': 'Sales order updated successfully'}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error updating sales order: {exc}'}), 500


@sales_bp.route('/sales-orders/<int:id>/status', methods=['PUT'])
@require_jwt_auth
def update_sales_order_status(id):
    workspace_id = _get_workspace_id()
    so = SalesOrder.query.filter_by(id=id, workspace_id=workspace_id).first()
    if not so:
        return jsonify({'success': False, 'data': None, 'message': 'Sales order not found'}), 404

    status = (request.get_json(silent=True) or {}).get('status')
    if status not in ['draft', 'approved', 'fulfilled', 'cancelled']:
        return jsonify({'success': False, 'data': None, 'message': 'Invalid status value'}), 400

    so.status = status
    so.updated_at = datetime.now(timezone.utc)
    try:
        db.session.commit()
        return jsonify({'success': True, 'data': so.to_dict(include_items=False), 'message': 'Sales order status updated successfully'}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error updating status: {exc}'}), 500


# ========== INVOICE ENDPOINTS ==========

@sales_bp.route('/invoices', methods=['GET'])
@require_jwt_auth
def get_invoices():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    offset = (page - 1) * limit
    status = request.args.get('status')

    workspace_id = _get_workspace_id()
    query = Invoice.query.filter_by(workspace_id=workspace_id)
    if status:
        query = query.filter_by(payment_status=status)

    total = query.count()
    invoices = query.order_by(Invoice.created_at.desc()).offset(offset).limit(limit).all()
    return jsonify({'success': True, 'data': {'count': total, 'page': page, 'limit': limit, 'results': [inv.to_dict() for inv in invoices]}, 'message': 'Invoices retrieved successfully'}), 200


@sales_bp.route('/invoices/<int:id>', methods=['GET'])
@require_jwt_auth
def get_invoice(id):
    workspace_id = _get_workspace_id()
    invoice = Invoice.query.filter_by(id=id, workspace_id=workspace_id).first()
    if not invoice:
        return jsonify({'success': False, 'data': None, 'message': 'Invoice not found'}), 404
    data = invoice.to_dict()
    data['payments'] = [p.to_dict() for p in invoice.payments]
    return jsonify({'success': True, 'data': data, 'message': 'Invoice retrieved successfully'}), 200


@sales_bp.route('/invoices', methods=['POST'])
@require_jwt_auth
def create_invoice():
    data = request.get_json(silent=True) or {}
    so_id = data.get('so_id')
    if not so_id:
        return jsonify({'success': False, 'data': None, 'message': 'Sales order ID is required'}), 400

    workspace_id = _get_workspace_id()
    so = SalesOrder.query.filter_by(id=so_id, workspace_id=workspace_id).first()
    if not so:
        return jsonify({'success': False, 'data': None, 'message': 'Sales order not found'}), 404
    existing = Invoice.query.filter_by(so_id=so_id, workspace_id=workspace_id).first()
    if existing:
        return jsonify({'success': False, 'data': None, 'message': 'Invoice already exists for this order'}), 400

    invoice = Invoice(
        invoice_number=_generate_invoice_number(workspace_id),
        so_id=so_id,
        invoice_date=datetime.strptime(data.get('invoice_date', date.today().isoformat()), '%Y-%m-%d').date(),
        due_date=datetime.strptime(data.get('due_date', date.today().isoformat()), '%Y-%m-%d').date(),
        total_amount=so.total_amount,
        tax_amount=so.tax_amount,
        net_amount=so.net_amount,
        payment_status='unpaid',
        workspace_id=workspace_id,
        created_by=_current_user().get('user_id') if _current_user() else None,
    )
    try:
        db.session.add(invoice)
        db.session.flush()
        _auto_post_invoice_tax(invoice, workspace_id)
        db.session.commit()
        return jsonify({'success': True, 'data': invoice.to_dict(), 'message': 'Invoice created successfully'}), 201
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error creating invoice: {exc}'}), 500


@sales_bp.route('/invoices/<int:id>/payment', methods=['POST'])
@require_jwt_auth
def record_payment(id):
    workspace_id = _get_workspace_id()
    invoice = Invoice.query.filter_by(id=id, workspace_id=workspace_id).first()
    if not invoice:
        return jsonify({'success': False, 'data': None, 'message': 'Invoice not found'}), 404

    data = request.get_json(silent=True) or {}
    amount_paid = data.get('amount_paid')
    if amount_paid is None:
        return jsonify({'success': False, 'data': None, 'message': 'Payment amount is required'}), 400

    amount_paid = float(amount_paid)
    payment = InvoicePayment(
        invoice_id=id,
        amount_paid=amount_paid,
        payment_date=datetime.strptime(data.get('payment_date', date.today().isoformat()), '%Y-%m-%d').date(),
        payment_method=data.get('payment_method', 'bank_transfer'),
        reference_number=data.get('reference_number'),
        notes=data.get('notes'),
        created_by=_current_user().get('user_id') if _current_user() else None,
    )

    invoice.paid_amount += amount_paid
    if invoice.paid_amount >= invoice.net_amount:
        invoice.payment_status = 'paid'
        invoice.payment_date = payment.payment_date
        invoice.payment_method = payment.payment_method
    elif invoice.paid_amount > 0:
        invoice.payment_status = 'partial'

    try:
        db.session.add(payment)
        db.session.commit()
        return jsonify({'success': True, 'data': invoice.to_dict(), 'message': 'Payment recorded successfully'}), 201
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error recording payment: {exc}'}), 500


@sales_bp.route('/orders/dispatch', methods=['POST'])
@require_jwt_auth
def create_dispatch():
    data = request.get_json(silent=True) or {}
    so_id = data.get('so_id')
    if not so_id:
        return jsonify({'success': False, 'data': None, 'message': 'Sales order ID is required'}), 400

    workspace_id = _get_workspace_id()
    so = SalesOrder.query.filter_by(id=so_id, workspace_id=workspace_id).first()
    if not so:
        return jsonify({'success': False, 'data': None, 'message': 'Sales order not found'}), 404
    existing = Dispatch.query.filter_by(so_id=so_id).first()
    if existing:
        return jsonify({'success': False, 'data': None, 'message': 'Dispatch already recorded for this order'}), 400

    dispatch = Dispatch(
        so_id=so_id,
        dispatch_date=datetime.strptime(data.get('dispatch_date', date.today().isoformat()), '%Y-%m-%d').date(),
        vehicle=data.get('vehicle'),
        tracking_number=data.get('tracking_number'),
        status='dispatched',
    )
    so.status = 'fulfilled'
    so.updated_at = datetime.now(timezone.utc)

    try:
        db.session.add(dispatch)
        db.session.commit()
        return jsonify({'success': True, 'data': dispatch.to_dict(), 'message': 'Dispatch recorded successfully'}), 201
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error recording dispatch: {exc}'}), 500

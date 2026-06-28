from flask import Blueprint, request, jsonify
from sqlalchemy import func
from datetime import datetime, date, timezone

from app.db import db
from app.models import SalesOrder, SalesOrderItem, Invoice, InvoicePayment, Dispatch, Distributor, Retailer
from app.routes.auth import require_jwt_auth

sales_bp = Blueprint('sales', __name__, url_prefix='/api/v1')


def _current_user():
    return getattr(request, 'user', None)


def _generate_so_number():
    year = datetime.now(timezone.utc).year
    last = SalesOrder.query.filter(SalesOrder.so_number.like(f'SO-{year}-%')).order_by(SalesOrder.id.desc()).first()
    counter = 1 if not last else int(last.so_number.rsplit('-', 1)[-1]) + 1
    return f'SO-{year}-{counter:04d}'


def _generate_invoice_number():
    year = datetime.now(timezone.utc).year
    last = Invoice.query.filter(Invoice.invoice_number.like(f'INV-{year}-%')).order_by(Invoice.id.desc()).first()
    counter = 1 if not last else int(last.invoice_number.rsplit('-', 1)[-1]) + 1
    return f'INV-{year}-{counter:04d}'


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

    query = SalesOrder.query
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
    so = db.session.get(SalesOrder, id)
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

    distributor = db.session.get(Distributor, distributor_id)
    if not distributor:
        return jsonify({'success': False, 'data': None, 'message': 'Distributor not found'}), 404
    retailer = db.session.get(Retailer, retailer_id)
    if not retailer:
        return jsonify({'success': False, 'data': None, 'message': 'Retailer not found'}), 404

    so = SalesOrder(
        so_number=_generate_so_number(),
        distributor_id=distributor_id,
        retailer_id=retailer_id,
        order_date=datetime.strptime(data.get('order_date', date.today().isoformat()), '%Y-%m-%d').date(),
        so_date=datetime.strptime(data.get('so_date', date.today().isoformat()), '%Y-%m-%d').date(),
        status='draft',
        created_by=_current_user().get('user_id') if _current_user() else None,
    )

    total_amount = 0.0
    for item in items:
        quantity = float(item.get('quantity', 0))
        unit_price = float(item.get('unit_price', 0))
        line_total = quantity * unit_price
        total_amount += line_total
        so.items.append(SalesOrderItem(product_code=item.get('product_code'), product_name=item.get('product_name'), quantity=quantity, unit_price=unit_price, line_total=line_total))

    tax_rate = float(data.get('tax_rate', 0.0)) / 100.0
    so.total_amount = total_amount
    so.tax_amount = total_amount * tax_rate
    so.net_amount = so.total_amount + so.tax_amount

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
    so = db.session.get(SalesOrder, id)
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
        so.distributor_id = data.get('distributor_id')
    if 'retailer_id' in data:
        so.retailer_id = data.get('retailer_id')

    if 'items' in data:
        SalesOrderItem.query.filter_by(so_id=id).delete()
        total_amount = 0.0
        for item in data['items']:
            quantity = float(item.get('quantity', 0))
            unit_price = float(item.get('unit_price', 0))
            line_total = quantity * unit_price
            total_amount += line_total
            db.session.add(SalesOrderItem(so_id=id, product_code=item.get('product_code'), product_name=item.get('product_name'), quantity=quantity, unit_price=unit_price, line_total=line_total))
        so.total_amount = total_amount
        so.tax_amount = total_amount * float(data.get('tax_rate', 0.0)) / 100.0
        so.net_amount = so.total_amount + so.tax_amount

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
    so = db.session.get(SalesOrder, id)
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

    query = Invoice.query
    if status:
        query = query.filter_by(payment_status=status)

    total = query.count()
    invoices = query.order_by(Invoice.created_at.desc()).offset(offset).limit(limit).all()
    return jsonify({'success': True, 'data': {'count': total, 'page': page, 'limit': limit, 'results': [inv.to_dict() for inv in invoices]}, 'message': 'Invoices retrieved successfully'}), 200


@sales_bp.route('/invoices/<int:id>', methods=['GET'])
@require_jwt_auth
def get_invoice(id):
    invoice = db.session.get(Invoice, id)
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

    so = db.session.get(SalesOrder, so_id)
    if not so:
        return jsonify({'success': False, 'data': None, 'message': 'Sales order not found'}), 404
    existing = Invoice.query.filter_by(so_id=so_id).first()
    if existing:
        return jsonify({'success': False, 'data': None, 'message': 'Invoice already exists for this order'}), 400

    invoice = Invoice(
        invoice_number=_generate_invoice_number(),
        so_id=so_id,
        invoice_date=datetime.strptime(data.get('invoice_date', date.today().isoformat()), '%Y-%m-%d').date(),
        due_date=datetime.strptime(data.get('due_date', date.today().isoformat()), '%Y-%m-%d').date(),
        total_amount=so.total_amount,
        tax_amount=so.tax_amount,
        net_amount=so.net_amount,
        payment_status='unpaid',
        created_by=_current_user().get('user_id') if _current_user() else None,
    )
    try:
        db.session.add(invoice)
        db.session.commit()
        return jsonify({'success': True, 'data': invoice.to_dict(), 'message': 'Invoice created successfully'}), 201
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error creating invoice: {exc}'}), 500


@sales_bp.route('/invoices/<int:id>/payment', methods=['POST'])
@require_jwt_auth
def record_payment(id):
    invoice = db.session.get(Invoice, id)
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

    so = db.session.get(SalesOrder, so_id)
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

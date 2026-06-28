import uuid
from datetime import date, datetime, timezone

from app.db import db


def utcnow():
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False, default='admin')
    workspace_id = db.Column(db.String(100), nullable=True, default='default')
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'role': self.role,
            'workspace_id': self.workspace_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Distributor(db.Model):
    __tablename__ = 'distributors'

    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, nullable=False, index=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(255), nullable=False, index=True)
    gst_number = db.Column(db.String(15), unique=True, nullable=True, index=True)
    territory = db.Column(db.String(100), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    state = db.Column(db.String(100), nullable=True)
    pin_code = db.Column(db.String(10), nullable=True)
    address = db.Column(db.Text, nullable=True)
    credit_limit = db.Column(db.Float, default=0.0)
    contact_person = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(15), nullable=True, index=True)
    email = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default='active')
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    retailers = db.relationship('Retailer', backref='distributor', cascade='all, delete-orphan')
    sales_orders = db.relationship('SalesOrder', backref='distributor', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'uuid': self.uuid,
            'name': self.name,
            'gst_number': self.gst_number,
            'territory': self.territory,
            'city': self.city,
            'state': self.state,
            'pin_code': self.pin_code,
            'address': self.address,
            'credit_limit': self.credit_limit,
            'contact_person': self.contact_person,
            'phone': self.phone,
            'email': self.email,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Retailer(db.Model):
    __tablename__ = 'retailers'

    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, nullable=False, index=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(255), nullable=False, index=True)
    gst_number = db.Column(db.String(15), unique=True, nullable=True, index=True)
    distributor_id = db.Column(db.Integer, db.ForeignKey('distributors.id'), nullable=False, index=True)
    territory = db.Column(db.String(100), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    state = db.Column(db.String(100), nullable=True)
    pin_code = db.Column(db.String(10), nullable=True)
    address = db.Column(db.Text, nullable=True)
    store_type = db.Column(db.String(50), nullable=True)
    contact_person = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(15), nullable=True, index=True)
    email = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default='active')
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    sales_orders = db.relationship('SalesOrder', backref='retailer', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'uuid': self.uuid,
            'name': self.name,
            'gst_number': self.gst_number,
            'distributor_id': self.distributor_id,
            'territory': self.territory,
            'city': self.city,
            'state': self.state,
            'pin_code': self.pin_code,
            'address': self.address,
            'store_type': self.store_type,
            'contact_person': self.contact_person,
            'phone': self.phone,
            'email': self.email,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class SalesOrder(db.Model):
    __tablename__ = 'sales_orders'

    id = db.Column(db.Integer, primary_key=True)
    so_number = db.Column(db.String(50), unique=True, nullable=False, index=True)
    distributor_id = db.Column(db.Integer, db.ForeignKey('distributors.id'), nullable=False, index=True)
    retailer_id = db.Column(db.Integer, db.ForeignKey('retailers.id'), nullable=False, index=True)
    order_date = db.Column(db.Date, nullable=False, default=date.today)
    so_date = db.Column(db.Date, nullable=False, default=date.today)
    total_amount = db.Column(db.Float, default=0.0)
    tax_amount = db.Column(db.Float, default=0.0)
    net_amount = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='draft', index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    items = db.relationship('SalesOrderItem', backref='sales_order', cascade='all, delete-orphan')
    invoices = db.relationship('Invoice', backref='sales_order', cascade='all, delete-orphan')
    dispatch = db.relationship('Dispatch', backref='sales_order', uselist=False, cascade='all, delete-orphan')

    def to_dict(self, include_items=True):
        data = {
            'id': self.id,
            'so_number': self.so_number,
            'distributor_id': self.distributor_id,
            'retailer_id': self.retailer_id,
            'order_date': self.order_date.isoformat() if self.order_date else None,
            'so_date': self.so_date.isoformat() if self.so_date else None,
            'total_amount': self.total_amount,
            'tax_amount': self.tax_amount,
            'net_amount': self.net_amount,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_items:
            data['items'] = [item.to_dict() for item in self.items]
        return data


class SalesOrderItem(db.Model):
    __tablename__ = 'sales_order_items'

    id = db.Column(db.Integer, primary_key=True)
    so_id = db.Column(db.Integer, db.ForeignKey('sales_orders.id'), nullable=False, index=True)
    product_code = db.Column(db.String(100), nullable=False)
    product_name = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    line_total = db.Column(db.Float, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'so_id': self.so_id,
            'product_code': self.product_code,
            'product_name': self.product_name,
            'quantity': self.quantity,
            'unit_price': self.unit_price,
            'line_total': self.line_total,
        }


class Invoice(db.Model):
    __tablename__ = 'invoices'

    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(50), unique=True, nullable=False, index=True)
    so_id = db.Column(db.Integer, db.ForeignKey('sales_orders.id'), nullable=False, index=True)
    invoice_date = db.Column(db.Date, nullable=False, default=date.today)
    due_date = db.Column(db.Date, nullable=False)
    total_amount = db.Column(db.Float, default=0.0)
    tax_amount = db.Column(db.Float, default=0.0)
    net_amount = db.Column(db.Float, default=0.0)
    paid_amount = db.Column(db.Float, default=0.0)
    payment_status = db.Column(db.String(20), default='unpaid', index=True)
    payment_date = db.Column(db.Date, nullable=True)
    payment_method = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    payments = db.relationship('InvoicePayment', backref='invoice', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'invoice_number': self.invoice_number,
            'so_id': self.so_id,
            'invoice_date': self.invoice_date.isoformat() if self.invoice_date else None,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'total_amount': self.total_amount,
            'tax_amount': self.tax_amount,
            'net_amount': self.net_amount,
            'paid_amount': self.paid_amount,
            'payment_status': self.payment_status,
            'payment_date': self.payment_date.isoformat() if self.payment_date else None,
            'payment_method': self.payment_method,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class InvoicePayment(db.Model):
    __tablename__ = 'invoice_payments'

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=False, index=True)
    amount_paid = db.Column(db.Float, nullable=False)
    payment_date = db.Column(db.Date, nullable=False, default=date.today)
    payment_method = db.Column(db.String(50), nullable=False)
    reference_number = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'invoice_id': self.invoice_id,
            'amount_paid': self.amount_paid,
            'payment_date': self.payment_date.isoformat() if self.payment_date else None,
            'payment_method': self.payment_method,
            'reference_number': self.reference_number,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Dispatch(db.Model):
    __tablename__ = 'dispatch'

    id = db.Column(db.Integer, primary_key=True)
    so_id = db.Column(db.Integer, db.ForeignKey('sales_orders.id'), nullable=False, unique=True, index=True)
    dispatch_date = db.Column(db.Date, nullable=False, default=date.today)
    vehicle = db.Column(db.String(100), nullable=True)
    tracking_number = db.Column(db.String(100), nullable=True, unique=True)
    status = db.Column(db.String(20), default='dispatched')
    delivery_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'so_id': self.so_id,
            'dispatch_date': self.dispatch_date.isoformat() if self.dispatch_date else None,
            'vehicle': self.vehicle,
            'tracking_number': self.tracking_number,
            'status': self.status,
            'delivery_date': self.delivery_date.isoformat() if self.delivery_date else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

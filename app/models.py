import uuid
from datetime import date, datetime, timezone
import hashlib
import hmac

from app.db import db


def utcnow():
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=True, index=True)
    role = db.Column(db.String(50), nullable=False, default='unassigned')
    status = db.Column(db.String(20), nullable=False, default='active')
    workspace_id = db.Column(db.String(100), nullable=True, default='default')
    full_name = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    gdrive_access_token = db.Column(db.String(255), nullable=True)
    gdrive_refresh_token = db.Column(db.String(255), nullable=True)
    gdrive_connected = db.Column(db.Boolean, default=False)
    gdrive_email = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def set_password(self, password):
        """Hash and set password"""
        self.password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    def check_password(self, password):
        """Verify password"""
        return self.password_hash == hashlib.sha256(password.encode()).hexdigest()

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'role': self.role,
            'status': self.status,
            'workspace_id': self.workspace_id,
            'full_name': self.full_name,
            'phone': self.phone,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Distributor(db.Model):
    __tablename__ = 'distributors'

    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, nullable=False, index=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(255), nullable=False, index=True)
    gst_number = db.Column(db.String(15), nullable=True, index=True)  # uniqueness enforced via partial index (active-only, per workspace) — see migration a2b4c6d8e0f2
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
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
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
            'workspace_id': self.workspace_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Retailer(db.Model):
    __tablename__ = 'retailers'

    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, nullable=False, index=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(255), nullable=False, index=True)
    gst_number = db.Column(db.String(15), nullable=True, index=True)  # uniqueness enforced via partial index (active-only, per workspace) — see migration a2b4c6d8e0f2
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
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
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
            'workspace_id': self.workspace_id,
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
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
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
            'workspace_id': self.workspace_id,
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
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
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


class FinanceAccount(db.Model):
    __tablename__ = 'finance_accounts'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    account_type = db.Column(db.String(50), nullable=False, default='asset')
    opening_balance = db.Column(db.Float, default=0.0)
    notes = db.Column(db.Text, nullable=True)
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class GSTReturn(db.Model):
    __tablename__ = 'gst_returns'

    id = db.Column(db.Integer, primary_key=True)
    period = db.Column(db.String(20), nullable=False, index=True)
    sales_amount = db.Column(db.Float, default=0.0)
    purchase_amount = db.Column(db.Float, default=0.0)
    tax_rate = db.Column(db.Float, default=0.0)
    tax_amount = db.Column(db.Float, default=0.0)
    filed_status = db.Column(db.String(20), default='draft')
    notes = db.Column(db.Text, nullable=True)
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class VATReturn(db.Model):
    __tablename__ = 'vat_returns'

    id = db.Column(db.Integer, primary_key=True)
    period = db.Column(db.String(20), nullable=False, index=True)
    sales_amount = db.Column(db.Float, default=0.0)
    purchase_amount = db.Column(db.Float, default=0.0)
    tax_rate = db.Column(db.Float, default=0.0)
    tax_amount = db.Column(db.Float, default=0.0)
    filed_status = db.Column(db.String(20), default='draft')
    notes = db.Column(db.Text, nullable=True)
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class AuditLog(db.Model):
    """Track all system activities for compliance and debugging"""
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    action = db.Column(db.String(100), nullable=False, index=True)
    resource_type = db.Column(db.String(50), nullable=False, index=True)
    resource_id = db.Column(db.Integer, nullable=True, index=True)
    details = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'action': self.action,
            'resource_type': self.resource_type,
            'resource_id': self.resource_id,
            'details': self.details,
            'ip_address': self.ip_address,
            'user_agent': self.user_agent,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Conversation(db.Model):
    __tablename__ = 'conversations'

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
    title = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(50), nullable=False, default='open', index=True)
    context = db.Column(db.JSON, nullable=True, default=dict)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    messages = db.relationship('ConversationMessage', back_populates='conversation', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'title': self.title,
            'status': self.status,
            'context': self.context or {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class ConversationMessage(db.Model):
    __tablename__ = 'conversation_messages'

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id'), nullable=False, index=True)
    role = db.Column(db.String(50), nullable=False, default='user')
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    conversation = db.relationship('Conversation', back_populates='messages')

    def to_dict(self):
        return {
            'id': self.id,
            'conversation_id': self.conversation_id,
            'role': self.role,
            'content': self.content,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Workflow(db.Model):
    __tablename__ = 'workflows'

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
    name = db.Column(db.String(255), nullable=False)
    definition = db.Column(db.JSON, nullable=True, default=dict)
    status = db.Column(db.String(50), nullable=False, default='draft', index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'name': self.name,
            'definition': self.definition or {},
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class WorkflowExecution(db.Model):
    __tablename__ = 'workflow_executions'

    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflows.id'), nullable=False, index=True)
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
    status = db.Column(db.String(50), nullable=False, default='running', index=True)
    input_data = db.Column(db.JSON, nullable=True, default=dict)
    output_data = db.Column(db.JSON, nullable=True, default=dict)
    started_at = db.Column(db.DateTime, default=utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)

    status_history = db.relationship(
        'WorkflowExecutionStatusHistory',
        backref='workflow_execution',
        lazy='dynamic',
        cascade='all, delete-orphan',
        order_by='WorkflowExecutionStatusHistory.created_at.asc()',
    )

    def to_dict(self):
        return {
            'id': self.id,
            'workflow_id': self.workflow_id,
            'workspace_id': self.workspace_id,
            'status': self.status,
            'input_data': self.input_data or {},
            'output_data': self.output_data or {},
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'status_history': [entry.to_dict() for entry in self.status_history.all()],
        }


class ConversationContext(db.Model):
    __tablename__ = 'conversation_context'

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id'), nullable=False, index=True)
    key = db.Column(db.String(100), nullable=False, index=True)
    value = db.Column(db.JSON, nullable=True, default=dict)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'conversation_id': self.conversation_id,
            'key': self.key,
            'value': self.value,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class EventRecord(db.Model):
    __tablename__ = 'events'

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
    event_type = db.Column(db.String(100), nullable=False, index=True)
    payload = db.Column(db.JSON, nullable=True, default=dict)
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'event_type': self.event_type,
            'workspace_id': self.workspace_id,
            'data': self.payload or {},
            'timestamp': self.created_at.isoformat() if self.created_at else None,
        }


class EventSubscription(db.Model):
    __tablename__ = 'event_subscriptions'

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
    event_type = db.Column(db.String(100), nullable=False, index=True)
    callback_url = db.Column(db.String(500), nullable=True)
    filters = db.Column(db.JSON, nullable=True, default=dict)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'event_type': self.event_type,
            'callback_url': self.callback_url,
            'filters': self.filters or {},
            'active': self.active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class KnowledgeGraphEntity(db.Model):
    __tablename__ = 'knowledge_graph_entities'

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(100), nullable=False, index=True)
    entity_id = db.Column(db.String(255), nullable=False, index=True)
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
    name = db.Column(db.String(255), nullable=True)
    properties = db.Column(db.JSON, nullable=True, default=dict)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (db.UniqueConstraint('entity_type', 'entity_id', 'workspace_id'),)

    def to_dict(self):
        return {
            'id': self.id,
            'entity_type': self.entity_type,
            'entity_id': self.entity_id,
            'workspace_id': self.workspace_id,
            'name': self.name,
            'properties': self.properties or {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class KnowledgeGraphRelationship(db.Model):
    __tablename__ = 'knowledge_graph_relationships'

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(100), nullable=False, index=True)
    entity_id = db.Column(db.String(255), nullable=False, index=True)
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
    relationship_type = db.Column(db.String(100), nullable=False, index=True)
    target_type = db.Column(db.String(100), nullable=True)
    target_id = db.Column(db.String(255), nullable=True)
    properties = db.Column(db.JSON, nullable=True, default=dict)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'entity_type': self.entity_type,
            'entity_id': self.entity_id,
            'workspace_id': self.workspace_id,
            'relationship_type': self.relationship_type,
            'target_type': self.target_type,
            'target_id': self.target_id,
            'properties': self.properties or {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class WorkflowStep(db.Model):
    __tablename__ = 'workflow_steps'

    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflows.id'), nullable=False, index=True)
    step_type = db.Column(db.String(50), nullable=False, default='manual')
    config = db.Column(db.JSON, nullable=True, default=dict)
    order_index = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'workflow_id': self.workflow_id,
            'step_type': self.step_type,
            'config': self.config or {},
            'order_index': self.order_index,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class WorkflowStepExecution(db.Model):
    __tablename__ = 'workflow_step_executions'

    id = db.Column(db.Integer, primary_key=True)
    workflow_execution_id = db.Column(db.Integer, db.ForeignKey('workflow_executions.id'), nullable=False, index=True)
    workflow_step_id = db.Column(db.Integer, db.ForeignKey('workflow_steps.id'), nullable=False, index=True)
    status = db.Column(db.String(50), nullable=False, default='pending', index=True)
    input_data = db.Column(db.JSON, nullable=True, default=dict)
    output_data = db.Column(db.JSON, nullable=True, default=dict)
    started_at = db.Column(db.DateTime, default=utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'workflow_execution_id': self.workflow_execution_id,
            'workflow_step_id': self.workflow_step_id,
            'status': self.status,
            'input_data': self.input_data or {},
            'output_data': self.output_data or {},
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
        }


class WorkflowExecutionStatusHistory(db.Model):
    __tablename__ = 'workflow_execution_status_history'

    id = db.Column(db.Integer, primary_key=True)
    workflow_execution_id = db.Column(db.Integer, db.ForeignKey('workflow_executions.id'), nullable=False, index=True)
    status = db.Column(db.String(50), nullable=False, index=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'workflow_execution_id': self.workflow_execution_id,
            'status': self.status,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class WorkflowNote(db.Model):
    __tablename__ = 'workflow_notes'

    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflows.id'), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    author = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'workflow_id': self.workflow_id,
            'content': self.content,
            'author': self.author,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class RuleExecution(db.Model):
    __tablename__ = 'rule_executions'

    id = db.Column(db.Integer, primary_key=True)
    rule_id = db.Column(db.Integer, db.ForeignKey('business_rules.id'), nullable=False, index=True)
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
    result = db.Column(db.JSON, nullable=True, default=dict)
    status = db.Column(db.String(50), nullable=False, default='completed', index=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'rule_id': self.rule_id,
            'workspace_id': self.workspace_id,
            'result': self.result or {},
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class AIResponse(db.Model):
    __tablename__ = 'ai_responses'

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id'), nullable=True, index=True)
    prompt = db.Column(db.Text, nullable=True)
    response_text = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), nullable=False, default='completed', index=True)
    model_name = db.Column(db.String(100), nullable=True)
    token_count = db.Column(db.Integer, nullable=False, default=0)
    latency_ms = db.Column(db.Integer, nullable=False, default=0)
    feedback = db.Column(db.JSON, nullable=True, default=dict)
    extra_metadata = db.Column(db.JSON, nullable=True, default=dict)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'conversation_id': self.conversation_id,
            'prompt': self.prompt,
            'response_text': self.response_text,
            'status': self.status,
            'model_name': self.model_name,
            'token_count': self.token_count,
            'latency_ms': self.latency_ms,
            'feedback': self.feedback or {},
            'metadata': self.extra_metadata or {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class BusinessRule(db.Model):
    __tablename__ = 'business_rules'

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
    rule_key = db.Column(db.String(255), nullable=False, unique=True, default=lambda: f"rule-{uuid.uuid4()}")
    rule_value = db.Column(db.Text, nullable=False, default='')
    name = db.Column(db.String(255), nullable=False)
    definition = db.Column(db.JSON, nullable=True, default=dict)
    priority = db.Column(db.Integer, nullable=False, default=0)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    is_locked = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'rule_key': self.rule_key,
            'rule_value': self.rule_value,
            'name': self.name,
            'definition': self.definition or {},
            'priority': self.priority,
            'enabled': self.enabled,
            'is_locked': self.is_locked,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Inventory(db.Model):
    """Track inventory levels by item and warehouse"""
    __tablename__ = 'inventory'

    id = db.Column(db.Integer, primary_key=True)
    item_code = db.Column(db.String(100), nullable=False, index=True)
    item_name = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(100), nullable=True, index=True)
    warehouse_id = db.Column(db.String(50), nullable=False, default='default', index=True)
    quantity_on_hand = db.Column(db.Float, nullable=False, default=0.0)
    reorder_level = db.Column(db.Float, nullable=False, default=0.0)
    reorder_quantity = db.Column(db.Float, nullable=False, default=0.0)
    unit_cost = db.Column(db.Float, nullable=False, default=0.0)
    unit_price = db.Column(db.Float, nullable=False, default=0.0)
    last_received = db.Column(db.Date, nullable=True)
    last_issued = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), default='active', index=True)
    workspace_id = db.Column(db.String(100), nullable=False, default='default', index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    movements = db.relationship('InventoryMovement', backref='inventory', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'item_code': self.item_code,
            'item_name': self.item_name,
            'category': self.category,
            'warehouse_id': self.warehouse_id,
            'quantity_on_hand': self.quantity_on_hand,
            'reorder_level': self.reorder_level,
            'reorder_quantity': self.reorder_quantity,
            'unit_cost': self.unit_cost,
            'unit_price': self.unit_price,
            'valuation': round(self.quantity_on_hand * self.unit_cost, 2),
            'last_received': self.last_received.isoformat() if self.last_received else None,
            'last_issued': self.last_issued.isoformat() if self.last_issued else None,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class InventoryMovement(db.Model):
    """Track all inventory movements (receipts, issues, adjustments)"""
    __tablename__ = 'inventory_movements'

    id = db.Column(db.Integer, primary_key=True)
    inventory_id = db.Column(db.Integer, db.ForeignKey('inventory.id'), nullable=False, index=True)
    movement_type = db.Column(db.String(50), nullable=False, index=True)  # receipt, issue, adjustment
    quantity = db.Column(db.Float, nullable=False)
    reason = db.Column(db.String(255), nullable=True)
    reference_number = db.Column(db.String(100), nullable=True)
    warehouse_from = db.Column(db.String(50), nullable=True)
    warehouse_to = db.Column(db.String(50), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

    def to_dict(self):
        return {
            'id': self.id,
            'inventory_id': self.inventory_id,
            'movement_type': self.movement_type,
            'quantity': self.quantity,
            'reason': self.reason,
            'reference_number': self.reference_number,
            'warehouse_from': self.warehouse_from,
            'warehouse_to': self.warehouse_to,
            'user_id': self.user_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

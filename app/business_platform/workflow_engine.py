"""
Platform Layer - Workflow Engine Service (Mock Implementation)
Provides state machines for business processes.

Phase 4 Status: Mock implementation with basic workflows
Production will: Load workflow definitions from database, support custom workflows
"""


import uuid
from datetime import datetime, timezone

from flask import current_app

from app.db import db
from app.models import Invoice, SalesOrder, Workflow, WorkflowExecution, WorkflowExecutionStatusHistory, WorkflowStep, WorkflowStepExecution
from app.business_platform.event_engine import EventEngine


class WorkflowEngine:
    """
    State machines for business processes (Order → Invoice → Payment, etc.)
    
    Phase 4: Hardcoded workflows
    Later: Workflows configurable by admin
    """
    
    # Workflow definitions
    WORKFLOWS = {
        'sales_order': {
            'name': 'Sales Order',
            'states': ['draft', 'confirmed', 'invoiced', 'paid', 'cancelled'],
            'initial': 'draft',
            'transitions': {
                'draft': ['confirmed', 'cancelled'],
                'confirmed': ['invoiced', 'cancelled'],
                'invoiced': ['paid'],
                'paid': [],
                'cancelled': []
            }
        },
        'purchase_order': {
            'name': 'Purchase Order',
            'states': ['draft', 'confirmed', 'received', 'paid', 'cancelled'],
            'initial': 'draft',
            'transitions': {
                'draft': ['confirmed', 'cancelled'],
                'confirmed': ['received', 'cancelled'],
                'received': ['paid'],
                'paid': [],
                'cancelled': []
            }
        },
        'invoice': {
            'name': 'Invoice',
            'states': ['draft', 'sent', 'paid', 'overdue', 'cancelled'],
            'initial': 'draft',
            'transitions': {
                'draft': ['sent', 'cancelled'],
                'sent': ['paid', 'overdue'],
                'overdue': ['paid'],
                'paid': [],
                'cancelled': []
            }
        }
    }
    
    @staticmethod
    def _ensure_app_context() -> None:
        try:
            current_app._get_current_object()
        except RuntimeError:
            from app.web_app import create_app

            create_app().app_context().push()

    @staticmethod
    def create_workflow(workspace_id: str, definition: dict) -> dict:
        WorkflowEngine._ensure_app_context()
        if not workspace_id:
            raise ValueError('workspace_id is required')
        workflow = Workflow(
            workspace_id=workspace_id,
            name=definition.get('name', 'Untitled workflow'),
            definition=definition.get('definition', definition),
            status='draft',
        )
        db.session.add(workflow)
        db.session.commit()
        return workflow.to_dict()

    @staticmethod
    def update_workflow(workflow_id: str, payload: dict) -> dict | None:
        WorkflowEngine._ensure_app_context()
        workflow = db.session.get(Workflow, int(workflow_id)) if str(workflow_id).isdigit() else None
        if workflow is None:
            return None
        for key, value in payload.items():
            if hasattr(workflow, key):
                setattr(workflow, key, value)
        db.session.commit()
        return workflow.to_dict()

    @staticmethod
    def delete_workflow(workflow_id: str) -> bool:
        WorkflowEngine._ensure_app_context()
        workflow = db.session.get(Workflow, int(workflow_id)) if str(workflow_id).isdigit() else None
        if workflow is None:
            return False
        db.session.delete(workflow)
        db.session.commit()
        return True

    @staticmethod
    def start_workflow(workflow_id: str, input_payload: dict | None = None) -> dict | None:
        WorkflowEngine._ensure_app_context()
        workflow = db.session.get(Workflow, int(workflow_id)) if str(workflow_id).isdigit() else None
        if workflow is None:
            return None
        execution = WorkflowExecution(workflow_id=workflow.id, workspace_id=workflow.workspace_id, status='running', input_data=input_payload or {})
        db.session.add(execution)
        db.session.flush()
        db.session.add(WorkflowExecutionStatusHistory(workflow_execution_id=execution.id, status='running', notes='Execution started'))
        db.session.commit()

        EventEngine.publish('workflow_started', workflow.workspace_id, {
            'workflow_id': workflow.id,
            'execution_id': execution.id,
            'status': 'running',
            'input_data': input_payload or {},
        })

        return execution.to_dict()

    @staticmethod
    def get_executions(workflow_id: str) -> list:
        WorkflowEngine._ensure_app_context()
        return [execution.to_dict() for execution in WorkflowExecution.query.filter_by(workflow_id=int(workflow_id)).all()]

    @staticmethod
    def get_execution(workflow_id: str, execution_id: str) -> dict | None:
        WorkflowEngine._ensure_app_context()
        execution = WorkflowExecution.query.filter_by(workflow_id=int(workflow_id), id=int(execution_id)).first()
        return execution.to_dict() if execution else None

    @staticmethod
    def update_execution(workflow_id: str, execution_id: str, payload: dict) -> dict | None:
        WorkflowEngine._ensure_app_context()
        execution = WorkflowExecution.query.filter_by(workflow_id=int(workflow_id), id=int(execution_id)).first()
        if execution is None:
            return None
        previous_status = execution.status
        for key, value in payload.items():
            if key == 'output_data':
                execution.output_data = value
            elif hasattr(execution, key):
                setattr(execution, key, value)
        new_status = payload.get('status')
        if new_status and new_status != previous_status:
            if new_status in ('completed', 'failed', 'cancelled'):
                execution.finished_at = datetime.now(timezone.utc)
            else:
                execution.finished_at = None
            db.session.add(WorkflowExecutionStatusHistory(
                workflow_execution_id=execution.id,
                status=new_status,
                notes='Status updated via execution update',
            ))
        db.session.commit()
        return execution.to_dict()

    @staticmethod
    def pause_execution(workflow_id: str, execution_id: str) -> dict | None:
        WorkflowEngine._ensure_app_context()
        execution = WorkflowExecution.query.filter_by(workflow_id=int(workflow_id), id=int(execution_id)).first()
        if execution is None:
            return None
        execution.status = 'paused'
        db.session.add(WorkflowExecutionStatusHistory(workflow_execution_id=execution.id, status='paused', notes='Execution paused'))
        db.session.commit()

        EventEngine.publish('workflow_paused', execution.workspace_id, {
            'workflow_id': execution.workflow_id,
            'execution_id': execution.id,
            'status': 'paused',
        })

        return execution.to_dict()

    @staticmethod
    def resume_execution(workflow_id: str, execution_id: str) -> dict | None:
        WorkflowEngine._ensure_app_context()
        execution = WorkflowExecution.query.filter_by(workflow_id=int(workflow_id), id=int(execution_id)).first()
        if execution is None:
            return None
        execution.status = 'running'
        execution.finished_at = None
        db.session.add(WorkflowExecutionStatusHistory(workflow_execution_id=execution.id, status='running', notes='Execution resumed'))
        db.session.commit()

        EventEngine.publish('workflow_resumed', execution.workspace_id, {
            'workflow_id': execution.workflow_id,
            'execution_id': execution.id,
            'status': 'running',
        })

        return execution.to_dict()

    @staticmethod
    def cancel_execution(workflow_id: str, execution_id: str) -> dict | None:
        WorkflowEngine._ensure_app_context()
        execution = WorkflowExecution.query.filter_by(workflow_id=int(workflow_id), id=int(execution_id)).first()
        if execution is None:
            return None
        execution.status = 'cancelled'
        execution.finished_at = datetime.now(timezone.utc)
        db.session.add(WorkflowExecutionStatusHistory(workflow_execution_id=execution.id, status='cancelled', notes='Execution cancelled'))
        db.session.commit()

        EventEngine.publish('workflow_cancelled', execution.workspace_id, {
            'workflow_id': execution.workflow_id,
            'execution_id': execution.id,
            'status': 'cancelled',
            'finished_at': execution.finished_at.isoformat() if execution.finished_at else None,
        })

        return execution.to_dict()

    @staticmethod
    def add_step(workflow_id: str, step: dict) -> dict | None:
        WorkflowEngine._ensure_app_context()
        workflow = db.session.get(Workflow, int(workflow_id)) if str(workflow_id).isdigit() else None
        if workflow is None:
            return None
        step_record = WorkflowStep(
            workflow_id=workflow.id,
            step_type=step.get('step_type', 'manual'),
            config=step.get('config', {}),
            order_index=step.get('order_index', 0),
        )
        db.session.add(step_record)
        db.session.flush()

        step_payload = {**step, 'id': str(step_record.id)}
        definition = workflow.definition or {}
        steps = definition.get('steps', [])
        steps.append(step_payload)
        workflow.definition = definition
        db.session.commit()
        return step_payload

    @staticmethod
    def update_step(workflow_id: str, step_id: str, payload: dict) -> dict | None:
        WorkflowEngine._ensure_app_context()
        workflow = db.session.get(Workflow, int(workflow_id)) if str(workflow_id).isdigit() else None
        if workflow is None:
            return None
        definition = workflow.definition or {}
        for step in definition.get('steps', []):
            if step.get('id') == step_id:
                step.update(payload)
                workflow.definition = definition
                db.session.commit()
                return step
        return None

    @staticmethod
    def delete_step(workflow_id: str, step_id: str) -> bool:
        WorkflowEngine._ensure_app_context()
        workflow = db.session.get(Workflow, int(workflow_id)) if str(workflow_id).isdigit() else None
        if workflow is None:
            return False
        definition = workflow.definition or {}
        steps = definition.get('steps', [])
        for index, step in enumerate(steps):
            if step.get('id') == step_id:
                steps.pop(index)
                workflow.definition = definition
                db.session.commit()
                return True
        return False

    @staticmethod
    def execute_step(payload: dict) -> dict:
        WorkflowEngine._ensure_app_context()
        execution = WorkflowStepExecution(
            workflow_execution_id=payload.get('workflow_execution_id'),
            workflow_step_id=payload.get('workflow_step_id'),
            status=payload.get('status', 'completed'),
            input_data=payload.get('input', {}),
            output_data=payload.get('output', {}),
        )
        db.session.add(execution)
        db.session.commit()
        return {
            'id': execution.id,
            'status': execution.status,
            'step_id': payload.get('workflow_step_id'),
            'input': execution.input_data,
            'output': execution.output_data,
        }

    @staticmethod
    def get_workflow(workflow_type: str) -> dict | None:
        """
        Get workflow definition by workflow id, saved name, or built-in type.

        Returns: Workflow dict or None
        """
        WorkflowEngine._ensure_app_context()

        if str(workflow_type).isdigit():
            workflow = db.session.get(Workflow, int(workflow_type))
            return workflow.to_dict() if workflow else None

        persisted = Workflow.query.filter_by(name=workflow_type).order_by(Workflow.created_at.desc()).first()
        if persisted:
            return persisted.to_dict()

        return WorkflowEngine.WORKFLOWS.get(workflow_type)

    @staticmethod
    def can_transition(workspace_id: str, order_id: str, current_state: str, to_state: str) -> tuple:
        """
        Check if transition is allowed.
        
        Args:
            workspace_id: Tenant identifier
            order_id: Order ID
            current_state: Current state
            to_state: Desired state
            
        Returns: (allowed: bool, reason: str)
        """
        workflow_type = 'sales_order'
        if order_id and str(order_id).isdigit():
            order_id_int = int(order_id)
            if SalesOrder.query.filter_by(id=order_id_int, workspace_id=workspace_id).first():
                workflow_type = 'sales_order'
            elif Invoice.query.filter_by(id=order_id_int, workspace_id=workspace_id).first():
                workflow_type = 'invoice'

        workflow = WorkflowEngine.get_workflow(workflow_type)
        
        if not workflow:
            return (False, f'Workflow {workflow_type} not found')
        
        if current_state not in workflow['transitions']:
            return (False, f'Invalid current state: {current_state}')
        
        allowed_transitions = workflow['transitions'].get(current_state, [])
        if to_state not in allowed_transitions:
            return (False, f'Cannot transition from {current_state} to {to_state}. Allowed: {allowed_transitions}')
        
        return (True, 'Transition allowed')
    
    @staticmethod
    def transition(workspace_id: str, order_id: str, to_state: str, current_state: str) -> tuple:
        """
        Execute state transition.
        
        Args:
            workspace_id: Tenant identifier
            order_id: Order ID
            to_state: New state
            current_state: Current state
            
        Returns: (success: bool, new_state: str)
        """
        allowed, reason = WorkflowEngine.can_transition(workspace_id, order_id, current_state, to_state)
        if not allowed:
            return (False, current_state)

        order = SalesOrder.query.filter_by(id=int(order_id), workspace_id=workspace_id).first()
        invoice = Invoice.query.filter_by(id=int(order_id), workspace_id=workspace_id).first()
        record = order or invoice

        if record is None:
            return (False, current_state)

        if isinstance(record, SalesOrder):
            record.status = to_state
        elif isinstance(record, Invoice):
            record.payment_status = to_state

        db.session.commit()

        EventEngine.publish('workflow_transitioned', workspace_id, {
            'record_id': int(order_id),
            'record_type': 'sales_order' if isinstance(record, SalesOrder) else 'invoice',
            'from_state': current_state,
            'to_state': to_state,
            'workspace_id': workspace_id,
        })

        return (True, to_state)

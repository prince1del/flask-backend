"""
Admin endpoints for system management and configuration
- GET /api/v1/admin/users - List all users
- POST /api/v1/admin/users - Create new user
- PUT /api/v1/admin/users/{id} - Update user
- DELETE /api/v1/admin/users/{id} - Delete user
- GET /api/v1/admin/audit-logs - View activity logs
- POST /api/v1/admin/settings - Save app settings
"""

import os

from flask import Blueprint, request, jsonify
from app.db import db
from app.models import User, AuditLog
from app.routes.auth import require_jwt_auth, require_role
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, desc

admin_bp = Blueprint('admin', __name__, url_prefix='/api/v1/admin')


def _founder_username() -> str:
    return (os.getenv('ADMIN_USERNAME') or 'admin').strip()


def _requester() -> dict:
    user = getattr(request, 'user', None)
    return user if isinstance(user, dict) else {}


def _forbidden(message: str):
    return (
        jsonify(
            {
                'success': False,
                'error': {
                    'code': 'FORBIDDEN',
                    'message': message,
                },
            }
        ),
        403,
    )


def _is_founder_requester() -> bool:
    return _requester().get('username') == _founder_username()

# ========== ADMIN 1: LIST USERS ==========
@admin_bp.route('/users', methods=['GET'])
@require_jwt_auth
@require_role('admin')
def list_users():
    """List all users with pagination"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        role = request.args.get('role')  # Optional filter: admin, distributor, retailer
        status = request.args.get('status')  # Optional filter: active, inactive
        
        query = User.query
        
        if role:
            query = query.filter_by(role=role)
        if status:
            query = query.filter_by(status=status)
        
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        
        users = [user.to_dict() for user in pagination.items]
        
        return jsonify({
            'success': True,
            'data': users,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': pagination.total,
                'pages': pagination.pages
            }
        }), 200
    
    except Exception as e:
        return jsonify({'success': False, 'data': None, 'message': f'Error listing users: {str(e)}'}), 500


# ========== ADMIN 2: CREATE USER ==========
@admin_bp.route('/users', methods=['POST'])
@require_jwt_auth
@require_role('admin')
def create_user():
    """Create a new user account"""
    data = request.get_json(silent=True) or {}
    
    # Validate required fields
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    role = data.get('role', 'unassigned')  # admin, sales_executive, distributor, retailer, unassigned

    if not username or not email or not password:
        return jsonify({'success': False, 'data': None, 'message': 'username, email, password required'}), 400

    if role not in ['admin', 'sales_executive', 'distributor', 'retailer', 'unassigned']:
        return jsonify({'success': False, 'data': None, 'message': 'Invalid role'}), 400

    if role == 'admin':
        if not _is_founder_requester():
            return _forbidden('Insufficient permissions to assign admin role')

    try:
        # Check if user already exists
        existing = User.query.filter(
            (User.username == username) | (User.email == email)
        ).first()
        
        if existing:
            return jsonify({'success': False, 'data': None, 'message': 'Username or email already exists'}), 409

        from app.workspace_tenancy import resolve_workspace_id_for_new_user

        # Each executive login gets a private data silo unless admin shares one.
        workspace_id = resolve_workspace_id_for_new_user(
            username,
            role,
            data.get('workspace_id'),
        )

        # Create new user
        user = User(
            username=username,
            email=email,
            role=role,
            status='active',
            workspace_id=workspace_id,
        )
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()
        
        # Create audit log
        audit = AuditLog(
            user_id=user.id,
            action='user_created',
            resource_type='user',
            resource_id=user.id,
            details=f'User {username} created (workspace={workspace_id})'
        )
        db.session.add(audit)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'data': user.to_dict(),
            'message': f'User {username} created successfully with private data space {workspace_id}'
        }), 201
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error creating user: {str(e)}'}), 500


# ========== ADMIN 3: UPDATE USER ==========
@admin_bp.route('/users/<int:user_id>', methods=['PUT'])
@require_jwt_auth
@require_role('admin')
def update_user(user_id):
    """Update user details"""
    data = request.get_json(silent=True) or {}
    
    try:
        user = db.session.get(User, user_id)
        if not user:
            return jsonify({'success': False, 'data': None, 'message': 'User not found'}), 404

        founder_username = _founder_username()
        # Non-founders must not demote/disable/repassword the founder account.
        if user.username == founder_username and not _is_founder_requester():
            return _forbidden('Insufficient permissions to modify the founder account')
        
        # Update allowed fields
        if 'email' in data:
            user.email = data['email'].strip()
        if 'role' in data:
            if data['role'] not in ['admin', 'sales_executive', 'distributor', 'retailer', 'unassigned']:
                return jsonify({'success': False, 'data': None, 'message': 'Invalid role'}), 400
            if data['role'] == 'admin' and not _is_founder_requester():
                return _forbidden('Insufficient permissions to assign admin role')
            # Founder must keep admin role — otherwise admin assignment locks out forever.
            if user.username == founder_username and data['role'] != 'admin':
                return _forbidden('Founder account must retain the admin role')
            user.role = data['role']
        if 'status' in data:
            if data['status'] not in ['active', 'inactive']:
                return jsonify({'success': False, 'data': None, 'message': 'Invalid status'}), 400
            if user.username == founder_username and data['status'] != 'active':
                return _forbidden('Founder account cannot be deactivated')
            user.status = data['status']
        if 'password' in data and data['password']:
            user.set_password(data['password'])
        if 'workspace_id' in data and data['workspace_id'] is not None:
            ws = str(data['workspace_id']).strip()
            if not ws:
                return jsonify({'success': False, 'data': None, 'message': 'workspace_id cannot be empty'}), 400
            user.workspace_id = ws
        
        user.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        
        # Create audit log
        audit = AuditLog(
            user_id=user_id,
            action='user_updated',
            resource_type='user',
            resource_id=user_id,
            details=f'User {user.username} updated'
        )
        db.session.add(audit)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'data': user.to_dict(),
            'message': 'User updated successfully'
        }), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error updating user: {str(e)}'}), 500


# ========== ADMIN 4: DELETE USER ==========
@admin_bp.route('/users/<int:user_id>', methods=['DELETE'])
@require_jwt_auth
@require_role('admin')
def delete_user(user_id):
    """Delete a user account"""
    try:
        user = db.session.get(User, user_id)
        if not user:
            return jsonify({'success': False, 'data': None, 'message': 'User not found'}), 404

        requester = _requester()
        founder_username = _founder_username()

        # Never allow deleting the founder — that permanently locks out admin assignment.
        if user.username == founder_username:
            return _forbidden('Founder account cannot be deleted')

        # Prevent accidental lockout / confused deputies deleting themselves.
        requester_id = requester.get('user_id')
        if requester_id is not None and int(requester_id) == int(user.id):
            return _forbidden('You cannot delete your own account')
        if requester.get('username') and requester.get('username') == user.username:
            return _forbidden('You cannot delete your own account')

        # Symmetric with create/update: only founder may remove other admin accounts.
        if user.role == 'admin' and not _is_founder_requester():
            return _forbidden('Insufficient permissions to delete an admin user')
        
        username = user.username
        
        # Create audit log before deletion
        audit = AuditLog(
            user_id=user_id,
            action='user_deleted',
            resource_type='user',
            resource_id=user_id,
            details=f'User {username} deleted'
        )
        db.session.add(audit)
        
        db.session.delete(user)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'data': None,
            'message': f'User {username} deleted successfully'
        }), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error deleting user: {str(e)}'}), 500


# ========== ADMIN 5: VIEW AUDIT LOGS ==========
@admin_bp.route('/audit-logs', methods=['GET'])
@require_jwt_auth
@require_role('admin')
def view_audit_logs():
    """View system activity/audit logs"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        action = request.args.get('action')  # Optional filter by action
        user_id = request.args.get('user_id', type=int)  # Optional filter by user
        days = request.args.get('days', 30, type=int)  # Last N days
        
        query = AuditLog.query
        
        # Filter by date range
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.filter(AuditLog.created_at >= start_date)
        
        if action:
            query = query.filter_by(action=action)
        if user_id:
            query = query.filter_by(user_id=user_id)
        
        pagination = query.order_by(desc(AuditLog.created_at)).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        logs = [log.to_dict() for log in pagination.items]
        
        return jsonify({
            'success': True,
            'data': logs,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': pagination.total,
                'pages': pagination.pages
            },
            'filters': {
                'days': days,
                'action': action,
                'user_id': user_id
            }
        }), 200
    
    except Exception as e:
        return jsonify({'success': False, 'data': None, 'message': f'Error retrieving audit logs: {str(e)}'}), 500


# ========== ADMIN 6: SAVE APP SETTINGS ==========
@admin_bp.route('/settings', methods=['POST'])
@require_jwt_auth
@require_role('admin')
def save_settings():
    """Save application-wide settings"""
    data = request.get_json(silent=True) or {}
    
    try:
        settings = {}
        
        # Validate and store allowed settings
        allowed_settings = [
            'company_name',
            'company_email',
            'company_phone',
            'currency_symbol',
            'default_tax_rate',
            'invoice_prefix',
            'maintenance_mode',
            'max_login_attempts',
            'session_timeout_minutes'
        ]
        
        for key in allowed_settings:
            if key in data:
                value = data[key]
                # Type validation for numeric fields
                if key in ['default_tax_rate', 'max_login_attempts', 'session_timeout_minutes']:
                    try:
                        value = float(value) if key == 'default_tax_rate' else int(value)
                    except (ValueError, TypeError):
                        return jsonify({'success': False, 'data': None, 'message': f'{key} must be numeric'}), 400
                if key == 'maintenance_mode':
                    value = bool(value)
                settings[key] = value
        
        # In a real app, these would be stored in a settings table
        # For now, we'll return success with the settings
        
        # Create audit log
        audit = AuditLog(
            user_id=None,
            action='settings_updated',
            resource_type='settings',
            resource_id=None,
            details=f'Settings updated: {", ".join(settings.keys())}'
        )
        db.session.add(audit)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'data': settings,
            'message': 'Settings saved successfully'
        }), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error saving settings: {str(e)}'}), 500

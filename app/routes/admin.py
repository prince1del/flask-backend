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

from flask import Blueprint, request, jsonify, current_app
from app.db import db
from app.models import User, AuditLog
from app.routes.auth import require_jwt_auth, require_role
from centralized_db_system.db import CentralizedDB
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, desc

admin_bp = Blueprint('admin', __name__, url_prefix='/api/v1/admin')


def _founder_username() -> str:
    """Sole platform controller — WORKSPACE_OWNER_USERNAME (default kunwar1del)."""
    return (os.getenv('WORKSPACE_OWNER_USERNAME') or 'kunwar1del').strip() or 'kunwar1del'


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
    """Only the supreme workspace owner may perform founder-gated admin actions."""
    requester = _requester()
    uname = str(requester.get('username') or '').strip().lower()
    if uname and uname == _founder_username().lower():
        return True
    return bool(requester.get('is_workspace_owner'))


def _auth_db() -> CentralizedDB:
    return CentralizedDB(str(current_app.config.get("DATABASE_PATH", "centralized_db.sqlite3")))


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

    # Checked before the generic role validation so callers get the real reason
    # instead of a bare "Invalid role".
    if role == 'admin':
        return _forbidden(
            'role=admin is disabled; only '
            f'{_founder_username()} holds platform admin powers via is_workspace_owner'
        )

    if role not in ['sales_executive', 'distributor', 'retailer', 'unassigned', 'hop_admin']:
        return jsonify({'success': False, 'data': None, 'message': 'Invalid role'}), 400

    try:
        from app.workspace_tenancy import resolve_workspace_id_for_new_user

        cdb = _auth_db()
        workspace_id = resolve_workspace_id_for_new_user(
            username,
            role,
            data.get('workspace_id'),
        )

        try:
            created = cdb.create_user(
                username,
                password,
                role=role,
                workspace_id=workspace_id,
                email=email,
            )
        except ValueError as exc:
            return jsonify({'success': False, 'data': None, 'message': str(exc)}), 409

        profile = cdb.get_user_profile(int(created["id"])) or created

        try:
            db.session.add(
                AuditLog(
                    user_id=int(created["id"]),
                    action='user_created',
                    resource_type='user',
                    resource_id=int(created["id"]),
                    details=f'User {username} created with role {role}',
                )
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        return jsonify({
            'success': True,
            'data': profile,
            'message': f'User {username} created successfully with private data space {workspace_id}'
        }), 201
    
    except Exception as e:
        return jsonify({'success': False, 'data': None, 'message': f'Error creating user: {str(e)}'}), 500


# ========== ADMIN 3: UPDATE USER ==========
@admin_bp.route('/users/<int:user_id>', methods=['PUT'])
@require_jwt_auth
@require_role('admin')
def update_user(user_id):
    """Update user details"""
    data = request.get_json(silent=True) or {}

    # is_workspace_owner lives only on CentralizedDB's real `users` table.
    # Production SQLAlchemy User.query is empty (GET /admin/users → total:0),
    # so this field must never depend on db.session.get(User, …).
    #
    # Auth: @require_role('admin') allows supreme workspace owners via bypass.
    # Promote (true) is API-forbidden — only boot promote_workspace_owner(
    # WORKSPACE_OWNER_USERNAME, default kunwar1del) may grant the flag.
    # Demote (false) is allowed for other accounts, never for that supreme user.
    if "is_workspace_owner" in data:
        other = {k for k in data.keys() if k != "is_workspace_owner"}
        if other:
            return jsonify({
                "success": False,
                "data": None,
                "message": (
                    "is_workspace_owner must be updated alone "
                    "(CentralizedDB path; cannot mix with SQLAlchemy fields)"
                ),
            }), 400
        want_owner = bool(data["is_workspace_owner"])
        if want_owner:
            return _forbidden(
                "Cannot grant is_workspace_owner via API; "
                "only WORKSPACE_OWNER_USERNAME is promoted on server boot"
            )
        try:
            cdb = _auth_db()
            target = cdb.get_user_profile(int(user_id))
            if not target:
                return jsonify(
                    {"success": False, "data": None, "message": "User not found"}
                ), 404
            supreme = (
                os.getenv("WORKSPACE_OWNER_USERNAME", "kunwar1del") or "kunwar1del"
            ).strip().lower()
            if str(target.get("username") or "").strip().lower() == supreme:
                return _forbidden(
                    f"Cannot revoke is_workspace_owner on {supreme}; "
                    "boot promote always restores it"
                )
            cdb.set_workspace_owner(int(user_id), False)
            refreshed = cdb.get_user_profile(int(user_id)) or {
                **target,
                "is_workspace_owner": False,
            }
            return jsonify({
                "success": True,
                "data": refreshed,
                "message": "is_workspace_owner updated",
            }), 200
        except Exception as e:
            return jsonify({
                "success": False,
                "data": None,
                "message": f"Error updating is_workspace_owner: {str(e)}",
            }), 500

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
            new_email = data['email'].strip()
            cdb = _auth_db()
            try:
                cdb.update_user_profile(
                    int(user.id),
                    email=new_email,
                )
            except ValueError as exc:
                return jsonify({'success': False, 'data': None, 'message': str(exc)}), 409
            refreshed = cdb.get_user_profile(int(user.id))
            if refreshed and refreshed.get("email") is not None:
                user.email = refreshed.get("email")
        if 'role' in data:
            if data['role'] not in ['sales_executive', 'distributor', 'retailer', 'unassigned', 'hop_admin']:
                return jsonify({'success': False, 'data': None, 'message': 'Invalid role'}), 400
            if data['role'] == 'admin':
                return _forbidden(
                    'role=admin is disabled; only '
                    f'{_founder_username()} holds platform admin powers via is_workspace_owner'
                )
            # Founder account must keep an active shell role.
            if user.username == founder_username and data['role'] not in {
                'sales_executive', 'hop_admin', 'admin'
            }:
                return _forbidden('Founder account must retain an active shell role')
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
    """Delete a user account.

    Production auth lives in CentralizedDB (DATABASE_PATH), not the SQLAlchemy
    User model — same dual-DB root cause as is_workspace_owner PUT. Prefer CDB;
    fall back to SQLAlchemy only for legacy/test rows that exist only there.
    """
    try:
        requester = _requester()
        founder_username = _founder_username()
        cdb = _auth_db()
        target = cdb.get_user_profile(int(user_id))

        if target:
            username = str(target.get("username") or "")
            role = str(target.get("role") or "")
            workspace_id = str(target.get("workspace_id") or "")

            if username.lower() == founder_username.lower():
                return _forbidden('Founder account cannot be deleted')

            requester_id = requester.get('user_id')
            if requester_id is not None and int(requester_id) == int(user_id):
                return _forbidden('You cannot delete your own account')
            if requester.get('username') and requester.get('username') == username:
                return _forbidden('You cannot delete your own account')

            # Protect production House of Prizm login — never wipe HoP shell access.
            if workspace_id == "house_of_prizm" and role == "hop_admin":
                return _forbidden('House of Prizm admin login cannot be deleted')

            if role == 'admin' and not _is_founder_requester():
                return _forbidden('Insufficient permissions to delete an admin user')
            if bool(target.get("is_workspace_owner")) and not _is_founder_requester():
                return _forbidden('Insufficient permissions to delete a workspace owner')

            deleted = cdb.delete_login_user(int(user_id))
            try:
                audit = AuditLog(
                    user_id=user_id,
                    action='user_deleted',
                    resource_type='user',
                    resource_id=user_id,
                    details=f'User {username} deleted (CentralizedDB)'
                )
                db.session.add(audit)
                db.session.commit()
            except Exception:
                db.session.rollback()

            return jsonify({
                'success': True,
                'data': deleted,
                'message': f'User {username} deleted successfully'
            }), 200

        # Legacy SQLAlchemy-only path (unit tests / empty CDB).
        user = db.session.get(User, user_id)
        if not user:
            return jsonify({'success': False, 'data': None, 'message': 'User not found'}), 404

        if user.username == founder_username:
            return _forbidden('Founder account cannot be deleted')

        requester_id = requester.get('user_id')
        if requester_id is not None and int(requester_id) == int(user.id):
            return _forbidden('You cannot delete your own account')
        if requester.get('username') and requester.get('username') == user.username:
            return _forbidden('You cannot delete your own account')

        if user.role == 'admin' and not _is_founder_requester():
            return _forbidden('Insufficient permissions to delete an admin user')

        username = user.username
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

    except ValueError as e:
        return jsonify({'success': False, 'data': None, 'message': str(e)}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error deleting user: {str(e)}'}), 500


# ========== ADMIN 4b: TENANCY ORPHAN CLEANUP ==========
# Leftover companies/workspace_registry rows from deleted signups (e.g. test
# accounts) that have zero remaining users. Founder-only: this is a bulk
# delete across tenant registry rows, not a single-user action.
@admin_bp.route('/tenancy/orphans', methods=['GET'])
@require_jwt_auth
@require_role('admin')
def list_tenancy_orphans():
    if not _is_founder_requester():
        return _forbidden('Only the founder can view tenancy orphans')
    try:
        cdb = _auth_db()
        orphans = cdb.find_orphaned_tenancy_rows()
        return jsonify({'success': True, 'data': {'orphans': orphans, 'count': len(orphans)}}), 200
    except Exception as e:
        return jsonify({'success': False, 'data': None, 'message': f'Error listing tenancy orphans: {str(e)}'}), 500


@admin_bp.route('/tenancy/orphans', methods=['DELETE'])
@require_jwt_auth
@require_role('admin')
def delete_tenancy_orphans():
    if not _is_founder_requester():
        return _forbidden('Only the founder can delete tenancy orphans')
    try:
        cdb = _auth_db()
        deleted = cdb.delete_orphaned_tenancy_rows()
        try:
            audit = AuditLog(
                user_id=_requester().get('user_id'),
                action='tenancy_orphans_deleted',
                resource_type='workspace_registry',
                resource_id=None,
                details=f'{len(deleted)} orphaned workspace/company rows deleted: '
                        f'{[d["workspace_id"] for d in deleted]}'
            )
            db.session.add(audit)
            db.session.commit()
        except Exception:
            db.session.rollback()
        return jsonify({'success': True, 'data': {'deleted': deleted, 'count': len(deleted)}}), 200
    except Exception as e:
        return jsonify({'success': False, 'data': None, 'message': f'Error deleting tenancy orphans: {str(e)}'}), 500


# ========== ADMIN 4c: MULTI-DEVICE APPROVAL ==========
# Every account is single-device by default: a new login kicks the previous
# phone/desktop out. Only the founder may grant a parallel-login exception.
@admin_bp.route('/users/<int:user_id>/multi-device', methods=['POST'])
@require_jwt_auth
@require_role('admin')
def set_user_multi_device(user_id):
    if not _is_founder_requester():
        return _forbidden('Only the owner can approve multi-device login')
    data = request.get_json(silent=True) or {}
    allowed = bool(data.get('allowed'))
    try:
        result = _auth_db().set_multi_device_allowed(int(user_id), allowed)
    except ValueError as exc:
        return jsonify({'success': False, 'data': None, 'message': str(exc)}), 404
    except Exception as e:
        return jsonify({'success': False, 'data': None, 'message': f'Error updating device policy: {str(e)}'}), 500
    try:
        audit = AuditLog(
            user_id=_requester().get('user_id'),
            action='multi_device_allowed' if allowed else 'multi_device_revoked',
            resource_type='user',
            resource_id=int(user_id),
            details=f"multi_device_allowed={allowed} for {result.get('username')}"
        )
        db.session.add(audit)
        db.session.commit()
    except Exception:
        db.session.rollback()
    return jsonify({'success': True, 'data': result}), 200


@admin_bp.route('/users/<int:user_id>/sessions', methods=['DELETE'])
@require_jwt_auth
@require_role('admin')
def revoke_user_sessions(user_id):
    """Force-logout a user everywhere (owner only)."""
    if not _is_founder_requester():
        return _forbidden('Only the owner can revoke sessions')
    try:
        _auth_db().clear_active_session(int(user_id))
    except Exception as e:
        return jsonify({'success': False, 'data': None, 'message': f'Error revoking sessions: {str(e)}'}), 500
    return jsonify({'success': True, 'data': {'user_id': int(user_id), 'revoked': True}}), 200


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

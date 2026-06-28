"""
Authentication Routes for NEXORA v1.1
JWT-based login, refresh, logout
"""

from flask import Blueprint, request, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime

bp = Blueprint('auth', __name__, url_prefix='/api/v1/auth')

# In-memory user store for demo (in production, use database)
USERS = {
    'mobile_test_admin': {
        'id': 1,
        'username': 'mobile_test_admin',
        'password_hash': generate_password_hash('mobile_test_admin_123'),
        'role': 'admin',
        'workspace_id': 'bombay_dyeing'
    },
    'mobile_test_user': {
        'id': 2,
        'username': 'mobile_test_user',
        'password_hash': generate_password_hash('mobile_test_user_123'),
        'role': 'user',
        'workspace_id': 'bombay_dyeing'
    },
    'founder_test': {
        'id': 3,
        'username': 'founder_test',
        'password_hash': generate_password_hash('founder_test_123'),
        'role': 'admin',
        'workspace_id': 'bombay_dyeing'
    }
}


def init_jwt_service(app):
    """Initialize JWT service with app secret key"""
    from jwt_service import JWTService
    jwt_service = JWTService(secret_key=app.config.get('SECRET_KEY', 'your-secret-key-change-in-production'))
    return jwt_service


@bp.route('/login', methods=['POST'])
def login():
    """Login with username + password, get JWT tokens"""
    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        
        if not username or not password:
            return jsonify({
                'success': False,
                'error': {
                    'code': 'MISSING_CREDENTIALS',
                    'message': 'Username and password required'
                }
            }), 400
        
        # Check user in store
        user = USERS.get(username)
        
        if not user or not check_password_hash(user['password_hash'], password):
            return jsonify({
                'success': False,
                'error': {
                    'code': 'INVALID_CREDENTIALS',
                    'message': 'Invalid username or password'
                }
            }), 401
        
        # Create tokens
        from jwt_service import JWTService
        jwt_service = JWTService(secret_key='your-secret-key-change-in-production')
        access_token, refresh_token = jwt_service.create_tokens(
            user_id=user['id'],
            username=user['username'],
            role=user['role'],
            workspace_id=user['workspace_id']
        )
        
        return jsonify({
            'success': True,
            'data': {
                'access_token': access_token,
                'refresh_token': refresh_token,
                'expires_in': jwt_service.access_token_expiry,
                'token_type': 'Bearer',
                'user': {
                    'id': user['id'],
                    'username': user['username'],
                    'role': user['role'],
                    'workspace_id': user['workspace_id']
                }
            }
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': {
                'code': 'LOGIN_ERROR',
                'message': str(e)
            }
        }), 500


@bp.route('/refresh', methods=['POST'])
def refresh():
    """Refresh access token using refresh token"""
    try:
        data = request.get_json()
        refresh_token = data.get('refresh_token')
        
        if not refresh_token:
            return jsonify({
                'success': False,
                'error': {
                    'code': 'NO_REFRESH_TOKEN',
                    'message': 'Refresh token required'
                }
            }), 400
        
        # Verify refresh token
        from jwt_service import JWTService
        jwt_service = JWTService(secret_key='your-secret-key-change-in-production')
        payload = jwt_service.verify_token(refresh_token)
        
        if 'error' in payload:
            return jsonify({
                'success': False,
                'error': {
                    'code': 'INVALID_REFRESH_TOKEN',
                    'message': payload['error']
                }
            }), 401
        
        if payload.get('type') != 'refresh':
            return jsonify({
                'success': False,
                'error': {
                    'code': 'INVALID_TOKEN_TYPE',
                    'message': 'Not a refresh token'
                }
            }), 401
        
        # Get user
        user = USERS.get(payload['username'])
        
        if not user:
            return jsonify({
                'success': False,
                'error': {
                    'code': 'USER_NOT_FOUND',
                    'message': 'User not found'
                }
            }), 404
        
        # Create new access token
        new_access_token, _ = jwt_service.create_tokens(
            user_id=user['id'],
            username=user['username'],
            role=user['role'],
            workspace_id=user['workspace_id']
        )
        
        return jsonify({
            'success': True,
            'data': {
                'access_token': new_access_token,
                'expires_in': jwt_service.access_token_expiry,
                'token_type': 'Bearer'
            }
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': {
                'code': 'REFRESH_ERROR',
                'message': str(e)
            }
        }), 500


@bp.route('/logout', methods=['POST'])
def logout():
    """Logout (client-side token deletion)"""
    # JWT is stateless, so logout is client-side token deletion
    # Server doesn't need to do anything
    return jsonify({
        'success': True,
        'data': {
            'message': 'Logged out successfully'
        }
    }), 200

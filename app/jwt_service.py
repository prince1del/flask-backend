"""
JWT Service for NEXORA
Handles token creation, verification, and authentication
"""

import jwt
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify


class JWTService:
    def __init__(self, secret_key, algorithm='HS256'):
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.access_token_expiry = 3600  # 1 hour
        self.refresh_token_expiry = 86400 * 7  # 7 days

    def create_tokens(self, user_id, username, role, workspace_id):
        """Create access token + refresh token"""
        now = datetime.utcnow()
        
        # Access token payload
        access_payload = {
            'user_id': user_id,
            'username': username,
            'role': role,
            'workspace_id': workspace_id,
            'iat': now,
            'exp': now + timedelta(seconds=self.access_token_expiry),
            'type': 'access'
        }
        
        # Refresh token payload
        refresh_payload = {
            'user_id': user_id,
            'username': username,
            'iat': now,
            'exp': now + timedelta(seconds=self.refresh_token_expiry),
            'type': 'refresh'
        }
        
        access_token = jwt.encode(access_payload, self.secret_key, algorithm=self.algorithm)
        refresh_token = jwt.encode(refresh_payload, self.secret_key, algorithm=self.algorithm)
        
        return access_token, refresh_token

    def verify_token(self, token):
        """Verify and decode token"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload
        except jwt.ExpiredSignatureError:
            return {'error': 'Token expired'}
        except jwt.InvalidTokenError:
            return {'error': 'Invalid token'}

    def require_auth(self, f):
        """Decorator to protect endpoints"""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            token = None
            
            # Extract token from Authorization header
            if 'Authorization' in request.headers:
                auth_header = request.headers['Authorization']
                try:
                    token = auth_header.split(' ')[1]  # "Bearer <token>"
                except IndexError:
                    return jsonify({
                        'success': False,
                        'error': {
                            'code': 'INVALID_HEADER',
                            'message': 'Invalid authorization header'
                        }
                    }), 401
            
            if not token:
                return jsonify({
                    'success': False,
                    'error': {
                        'code': 'NO_TOKEN',
                        'message': 'Missing authorization token'
                    }
                }), 401
            
            # Verify token
            payload = self.verify_token(token)
            if 'error' in payload:
                return jsonify({
                    'success': False,
                    'error': {
                        'code': 'INVALID_TOKEN',
                        'message': payload['error']
                    }
                }), 401
            
            # Attach user to request context
            request.user = payload
            return f(*args, **kwargs)
        
        return decorated_function

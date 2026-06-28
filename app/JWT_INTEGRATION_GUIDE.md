"""
JWT Integration Guide for NEXORA
How to protect existing endpoints with JWT
"""

# STEP 1: Update app/__init__.py to initialize JWT service

"""
In app/__init__.py, add:

from flask import Flask
from flask_cors import CORS
from jwt_service import JWTService

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # Initialize JWT service
    app.jwt_service = JWTService(secret_key=app.config.get('SECRET_KEY', 'your-secret-key'))
    
    # Initialize CORS
    CORS(app)
    
    # Register blueprints
    from app.routes import auth
    app.register_blueprint(auth.bp)
    
    # ... rest of initialization
    
    return app
"""


# STEP 2: Protect existing endpoints by adding @jwt_service.require_auth decorator

"""
Example 1: Protect GET /api/v1/workspaces

BEFORE:
    @bp.route('/workspaces', methods=['GET'])
    def list_workspaces():
        workspaces = get_all_workspaces()
        return jsonify(workspaces), 200

AFTER:
    @bp.route('/workspaces', methods=['GET'])
    @jwt_service.require_auth  # ← Add this line
    def list_workspaces():
        # Now request.user contains JWT payload
        user_id = request.user['user_id']
        workspace_id = request.user['workspace_id']
        
        workspaces = get_all_workspaces(workspace_id=workspace_id)
        return jsonify({
            'success': True,
            'data': workspaces
        }), 200
"""


# STEP 3: Update response format to standard format

"""
STANDARD RESPONSE FORMAT (Success):
{
    "success": true,
    "data": { ... }
}

STANDARD RESPONSE FORMAT (Error):
{
    "success": false,
    "error": {
        "code": "ERROR_CODE",
        "message": "Human readable message"
    }
}

Examples:

Success Response:
{
    "success": true,
    "data": {
        "workspaces": [
            {"id": "ws_001", "name": "Bombay Dyeing", "owner": "kunwar"}
        ]
    }
}

Error Response (401 Unauthorized):
{
    "success": false,
    "error": {
        "code": "NO_TOKEN",
        "message": "Missing authorization token"
    }
}

Error Response (400 Bad Request):
{
    "success": false,
    "error": {
        "code": "INVALID_INPUT",
        "message": "Schema validation failed"
    }
}
"""


# STEP 4: Apply to all data endpoints

"""
Endpoints that need @jwt_service.require_auth decorator:

Data Entry:
- POST /api/v1/workspaces/{id}/data

Verification:
- POST /api/v1/workspaces/{id}/verify

Analytics:
- GET /api/v1/workspaces/{id}/analytics

Schema:
- GET /api/v1/workspaces/{id}/schema
- PUT /api/v1/workspaces/{id}/schema

Reports:
- POST /api/v1/workspaces/{id}/reports/generate
- GET /api/v1/workspaces/{id}/reports/{report_id}
- GET /download/{report_id}

Workspaces:
- GET /api/v1/workspaces
- POST /api/v1/workspaces
- GET /api/v1/workspaces/{id}
- PUT /api/v1/workspaces/{id}
- DELETE /api/v1/workspaces/{id}

Health Check (does NOT need JWT):
- GET /health
"""


# STEP 5: How to use request.user in protected endpoints

"""
Once an endpoint is protected with @jwt_service.require_auth,
you have access to request.user containing:

{
    'user_id': 1,
    'username': 'mobile_test_admin',
    'role': 'admin',
    'workspace_id': 'bombay_dyeing',
    'iat': <issued_at_timestamp>,
    'exp': <expiration_timestamp>,
    'type': 'access'
}

Usage example:

@bp.route('/workspaces/{id}/data', methods=['POST'])
@jwt_service.require_auth
def submit_data(id):
    user_workspace = request.user['workspace_id']
    user_role = request.user['role']
    
    # Verify user owns this workspace
    if id != user_workspace:
        return jsonify({
            'success': False,
            'error': {
                'code': 'FORBIDDEN',
                'message': 'You do not have access to this workspace'
            }
        }), 403
    
    # Process data submission
    data = request.get_json()
    # ... save to database
    
    return jsonify({
        'success': True,
        'data': {'record_id': '...'}
    }), 201
"""


# TEST COMMANDS (after deployment)

"""
Test 1: Login and get tokens
curl -X POST https://flask-backend-wnlq.onrender.com/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "mobile_test_admin", "password": "mobile_test_admin_123"}'

Expected:
{
    "success": true,
    "data": {
        "access_token": "eyJhbGciOiJIUzI1NiIs...",
        "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
        "expires_in": 3600,
        "token_type": "Bearer",
        "user": {"id": 1, "username": "mobile_test_admin", "role": "admin"}
    }
}


Test 2: Access protected endpoint with token
curl -X GET https://flask-backend-wnlq.onrender.com/api/v1/workspaces \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."

Expected:
{
    "success": true,
    "data": [...]
}


Test 3: Access protected endpoint WITHOUT token (should fail)
curl -X GET https://flask-backend-wnlq.onrender.com/api/v1/workspaces

Expected:
{
    "success": false,
    "error": {
        "code": "NO_TOKEN",
        "message": "Missing authorization token"
    }
}
(Status: 401 Unauthorized)


Test 4: Refresh token
curl -X POST https://flask-backend-wnlq.onrender.com/api/v1/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "eyJhbGciOiJIUzI1NiIs..."}'

Expected:
{
    "success": true,
    "data": {
        "access_token": "eyJhbGciOiJIUzI1NiIs...",
        "expires_in": 3600,
        "token_type": "Bearer"
    }
}
"""

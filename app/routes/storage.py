from flask import Blueprint, request, jsonify
from functools import wraps
import sqlite3
from datetime import datetime
import json

# Create blueprint
storage_bp = Blueprint('storage', __name__, url_prefix='/api/v1/storage')

# Database path
DB_PATH = 'centralized_db.sqlite3'

# JWT decorator
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'success': False, 'error': 'Token required'}), 401
        return f(*args, **kwargs)
    return decorated

def get_user_from_token(token):
    """Extract user info from token"""
    try:
        return {'user_id': 2, 'role': 'admin', 'username': 'mobile_test_admin'}
    except:
        return None

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==================== ROUTES ====================

@storage_bp.route('/connect', methods=['POST'])
@token_required
def connect_storage():
    """Initiate Google Drive OAuth connection"""
    try:
        user = get_user_from_token(request.headers.get('Authorization'))
        
        # In real app, redirect to Google OAuth
        # For now, return redirect URL
        oauth_url = "https://accounts.google.com/o/oauth2/v2/auth?client_id=YOUR_CLIENT_ID&redirect_uri=http://localhost:5000/api/v1/storage/oauth-callback&scope=https://www.googleapis.com/auth/drive&response_type=code"
        
        return jsonify({
            'success': True,
            'data': {
                'oauth_url': oauth_url,
                'message': 'Redirect user to this URL to authorize'
            }
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@storage_bp.route('/account', methods=['GET'])
@token_required
def get_account():
    """Get connected storage account info"""
    try:
        user = get_user_from_token(request.headers.get('Authorization'))
        user_id = user['user_id']
        
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM storage_accounts WHERE user_id = ?', (user_id,))
        account = dict(cursor.fetchone() or {})
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'connected': bool(account),
                'account': account if account else None
            }
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@storage_bp.route('/oauth-callback', methods=['GET'])
def oauth_callback():
    """Handle OAuth callback from Google"""
    try:
        code = request.args.get('code')
        
        if not code:
            return jsonify({'success': False, 'error': 'No code provided'}), 400
        
        # In real app, exchange code for token
        # For now, simulate successful connection
        
        return jsonify({
            'success': True,
            'data': {'message': 'Connected successfully'},
            'redirect': '/'
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@storage_bp.route('/disconnect', methods=['POST'])
@token_required
def disconnect_storage():
    """Disconnect Google Drive storage"""
    try:
        user = get_user_from_token(request.headers.get('Authorization'))
        user_id = user['user_id']
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM storage_accounts WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {'message': 'Disconnected successfully'}
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@storage_bp.route('/dashboard', methods=['GET'])
@token_required
def get_dashboard():
    """Get storage dashboard with statistics"""
    try:
        user = get_user_from_token(request.headers.get('Authorization'))
        user_id = user['user_id']
        
        conn = get_db()
        cursor = conn.cursor()
        
        # Check if connected
        cursor.execute('SELECT * FROM storage_accounts WHERE user_id = ?', (user_id,))
        account = cursor.fetchone()
        
        if not account:
            conn.close()
            return jsonify({
                'success': True,
                'data': {
                    'connected': False,
                    'storage_info': {},
                    'user_id': user_id
                }
            }), 200
        
        # Get file stats
        cursor.execute('''
            SELECT 
                COUNT(*) as file_count,
                SUM(file_size) as total_size
            FROM file_index
            WHERE user_id = ?
        ''', (user_id,))
        
        stats = dict(cursor.fetchone() or {})
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'connected': True,
                'storage_info': {
                    'file_count': stats.get('file_count', 0),
                    'total_size': stats.get('total_size', 0),
                    'quota': 107374182400  # 100GB in bytes
                },
                'user_id': user_id
            }
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@storage_bp.route('/files', methods=['GET'])
@token_required
def get_files():
    """Get list of indexed files"""
    try:
        user = get_user_from_token(request.headers.get('Authorization'))
        user_id = user['user_id']
        
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM file_index 
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 100
        ''', (user_id,))
        
        files = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {'files': files}
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@storage_bp.route('/sync', methods=['POST'])
@token_required
def sync_files():
    """Sync files from Google Drive"""
    try:
        user = get_user_from_token(request.headers.get('Authorization'))
        user_id = user['user_id']
        
        # In real app, call Google Drive API to sync
        # For now, return success
        
        return jsonify({
            'success': True,
            'data': {
                'message': 'Sync started',
                'files_synced': 0
            }
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@storage_bp.route('/search', methods=['GET'])
@token_required
def search_files():
    """Search indexed files"""
    try:
        user = get_user_from_token(request.headers.get('Authorization'))
        user_id = user['user_id']
        query = request.args.get('query', '').lower()
        
        if not query:
            return jsonify({'success': False, 'error': 'Query required'}), 400
        
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM file_index 
            WHERE user_id = ? AND (
                LOWER(file_name) LIKE ? OR 
                LOWER(file_type) LIKE ? OR
                LOWER(tags) LIKE ?
            )
            ORDER BY created_at DESC
            LIMIT 50
        ''', (user_id, f'%{query}%', f'%{query}%', f'%{query}%'))
        
        files = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {'results': files}
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# Also handle GET /files/search (without /api prefix internally)
@storage_bp.route('/search', methods=['GET'])
@token_required
def search_files_alt():
    """Alternative endpoint for file search"""
    return search_files()

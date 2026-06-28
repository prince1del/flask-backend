from flask import Blueprint, request, jsonify
from functools import wraps
import sqlite3
from datetime import datetime
import json

# Create blueprint
target_achievement_bp = Blueprint('target_achievement', __name__, url_prefix='/api/v1/target-achievement')

# Database path
DB_PATH = 'centralized_db.sqlite3'

# JWT decorator (reuse from auth module)
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'success': False, 'error': 'Token required'}), 401
        return f(*args, **kwargs)
    return decorated

def get_user_from_token(token):
    """Extract user info from token (in real app, decode JWT)"""
    try:
        # For now, return admin user
        return {'user_id': 2, 'role': 'admin', 'username': 'mobile_test_admin'}
    except:
        return None

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        user = get_user_from_token(token)
        if not user or user['role'] != 'admin':
            return jsonify({'success': False, 'error': 'Admin only'}), 403
        return f(*args, **kwargs)
    return decorated

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==================== ROUTES ====================

@target_achievement_bp.route('/years', methods=['GET'])
@token_required
def get_years():
    """Get all fiscal years"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM target_achievement_years ORDER BY year DESC')
        years = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {'years': years}
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years', methods=['POST'])
@require_admin
def create_year():
    """Create new fiscal year"""
    try:
        data = request.get_json()
        year = data.get('year')
        target = data.get('target')
        
        if not year or target is None:
            return jsonify({'success': False, 'error': 'Year and target required'}), 400
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO target_achievement_years (year, target, created_at)
            VALUES (?, ?, ?)
        ''', (year, target, datetime.now().isoformat()))
        conn.commit()
        year_id = cursor.lastrowid
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {'year_id': year_id, 'year': year, 'target': target}
        }), 201
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years/<int:year_id>', methods=['PUT'])
@require_admin
def update_year(year_id):
    """Update fiscal year target"""
    try:
        data = request.get_json()
        target = data.get('target')
        
        if target is None:
            return jsonify({'success': False, 'error': 'Target required'}), 400
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE target_achievement_years 
            SET target = ?, updated_at = ?
            WHERE id = ?
        ''', (target, datetime.now().isoformat(), year_id))
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {'year_id': year_id, 'target': target}
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years/<int:year_id>/summary', methods=['GET'])
@token_required
def get_summary(year_id):
    """Get target vs achievement summary"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # Get year
        cursor.execute('SELECT * FROM target_achievement_years WHERE id = ?', (year_id,))
        year = dict(cursor.fetchone() or {})
        
        if not year:
            return jsonify({'success': False, 'error': 'Year not found'}), 404
        
        # Get total achievement
        cursor.execute('''
            SELECT SUM(amount) as total 
            FROM target_achievement_uploads 
            WHERE year_id = ?
        ''', (year_id,))
        result = cursor.fetchone()
        achievement = result['total'] or 0
        
        conn.close()
        
        target = year.get('target', 0)
        percentage = (achievement / target * 100) if target > 0 else 0
        
        return jsonify({
            'success': True,
            'data': {
                'year_id': year_id,
                'target': target,
                'achievement': achievement,
                'percentage': round(percentage, 2)
            }
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years/<int:year_id>/breakup', methods=['GET'])
@token_required
def get_breakup(year_id):
    """Get distributor-wise breakup"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                distributor_name,
                SUM(amount) as achievement,
                COUNT(*) as count
            FROM target_achievement_uploads
            WHERE year_id = ?
            GROUP BY distributor_name
            ORDER BY achievement DESC
        ''', (year_id,))
        
        breakup = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {'breakup': breakup}
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years/<int:year_id>/upload', methods=['POST'])
@require_admin
def upload_achievement(year_id):
    """Upload achievement file"""
    try:
        data = request.get_json()
        distributor = data.get('distributor_name')
        amount = data.get('amount')
        file_name = data.get('file_name')
        
        if not all([distributor, amount is not None, file_name]):
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO target_achievement_uploads 
            (year_id, distributor_name, amount, file_name, uploaded_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (year_id, distributor, amount, file_name, datetime.now().isoformat()))
        conn.commit()
        upload_id = cursor.lastrowid
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {'upload_id': upload_id}
        }), 201
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/summary', methods=['GET'])
@token_required
def get_overall_summary():
    """Get overall summary across all years"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                SUM(target) as total_target,
                SUM(COALESCE((SELECT SUM(amount) FROM target_achievement_uploads WHERE year_id = target_achievement_years.id), 0)) as total_achievement
            FROM target_achievement_years
        ''')
        
        result = dict(cursor.fetchone() or {})
        conn.close()
        
        target = result.get('total_target', 0) or 0
        achievement = result.get('total_achievement', 0) or 0
        percentage = (achievement / target * 100) if target > 0 else 0
        
        return jsonify({
            'success': True,
            'data': {
                'total_target': target,
                'total_achievement': achievement,
                'percentage': round(percentage, 2)
            }
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

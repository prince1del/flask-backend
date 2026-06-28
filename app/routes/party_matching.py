from flask import Blueprint, request, jsonify
from functools import wraps
import sqlite3
from datetime import datetime
import uuid
import json

# Create blueprint
party_matching_bp = Blueprint('party_matching', __name__, url_prefix='/api/v1/party-matching')

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

def levenshtein_distance(s1, s2):
    """Calculate similarity between two strings (0-1)"""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return 1.0
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    distance = previous_row[-1]
    max_len = max(len(s1), len(s2))
    return 1 - (distance / max_len)

def calculate_confidence(party1, party2):
    """Calculate matching confidence between two parties"""
    score = 0
    
    # GST match (40%)
    if party1.get('gst_number') and party2.get('gst_number'):
        if party1['gst_number'] == party2['gst_number']:
            score += 0.40
    
    # Mobile match (20%)
    if party1.get('mobile_number') and party2.get('mobile_number'):
        if party1['mobile_number'][-10:] == party2['mobile_number'][-10:]:
            score += 0.20
        elif party1['mobile_number'][-10:] == party2['mobile_number'][-10:]:
            score += 0.10
    
    # Email match (15%)
    if party1.get('email') and party2.get('email'):
        if party1['email'] == party2['email']:
            score += 0.15
        elif party1['email'].split('@')[0] == party2['email'].split('@')[0]:
            score += 0.08
    
    # Name similarity (15%)
    name1 = (party1.get('primary_name') or '').lower()
    name2 = (party2.get('primary_name') or '').lower()
    if name1 and name2:
        similarity = levenshtein_distance(name1, name2)
        score += similarity * 0.15
    
    # City match (5%)
    if party1.get('city') and party2.get('city'):
        if party1['city'].lower() == party2['city'].lower():
            score += 0.05
    
    # PIN match (3%)
    if party1.get('pin_code') and party2.get('pin_code'):
        if party1['pin_code'] == party2['pin_code']:
            score += 0.03
    
    # State match (2%)
    if party1.get('state') and party2.get('state'):
        if party1['state'].lower() == party2['state'].lower():
            score += 0.02
    
    return min(score * 100, 100)  # Return 0-100

# ==================== ROUTES ====================

@party_matching_bp.route('/search', methods=['GET'])
@token_required
def search_parties():
    """Search for parties by name (includes aliases)"""
    try:
        query = request.args.get('query', '').lower()
        
        if not query:
            return jsonify({'success': False, 'error': 'Query required'}), 400
        
        conn = get_db()
        cursor = conn.cursor()
        
        # Search in master_parties
        cursor.execute('''
            SELECT * FROM master_parties 
            WHERE LOWER(primary_name) LIKE ? OR LOWER(gst_number) LIKE ?
            LIMIT 20
        ''', (f'%{query}%', f'%{query}%'))
        
        results = [dict(row) for row in cursor.fetchall()]
        
        # Also search in aliases
        cursor.execute('''
            SELECT DISTINCT mp.* FROM master_parties mp
            JOIN party_aliases pa ON mp.party_uuid = pa.party_uuid
            WHERE LOWER(pa.alias_name) LIKE ?
            LIMIT 20
        ''', (f'%{query}%',))
        
        alias_results = [dict(row) for row in cursor.fetchall()]
        
        # Merge results
        seen = set()
        all_results = []
        for r in results + alias_results:
            uuid = r.get('party_uuid')
            if uuid not in seen:
                seen.add(uuid)
                all_results.append(r)
        
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {'results': all_results}
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@party_matching_bp.route('/find-matches', methods=['POST'])
@token_required
def find_matches():
    """Find potential matching parties"""
    try:
        data = request.get_json()
        
        party = {
            'party_type': data.get('party_type'),
            'primary_name': data.get('primary_name'),
            'gst_number': data.get('gst_number'),
            'mobile_number': data.get('mobile_number'),
            'email': data.get('email'),
            'city': data.get('city'),
            'state': data.get('state'),
            'pin_code': data.get('pin_code')
        }
        
        conn = get_db()
        cursor = conn.cursor()
        
        # Find similar parties
        cursor.execute('''
            SELECT * FROM master_parties 
            WHERE party_type = ?
        ''', (party['party_type'],))
        
        existing_parties = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        matches = []
        for existing in existing_parties:
            confidence = calculate_confidence(party, existing)
            if confidence >= 70:  # Only show >70% matches
                matches.append({
                    'target_party_uuid': existing['party_uuid'],
                    'target_party_name': existing['primary_name'],
                    'confidence_score': round(confidence, 2),
                    'category': 'very_high' if confidence >= 95 else 'high' if confidence >= 85 else 'possible'
                })
        
        # Sort by confidence
        matches = sorted(matches, key=lambda x: x['confidence_score'], reverse=True)
        
        return jsonify({
            'success': True,
            'data': {'matches': matches}
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@party_matching_bp.route('/create-master-party', methods=['POST'])
@token_required
def create_master_party():
    """Create new master party"""
    try:
        data = request.get_json()
        party_uuid = str(uuid.uuid4())
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO master_parties 
            (party_uuid, party_type, primary_name, gst_number, mobile_number, email, city, state, pin_code, status, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            party_uuid,
            data.get('party_type'),
            data.get('primary_name'),
            data.get('gst_number'),
            data.get('mobile_number'),
            data.get('email'),
            data.get('city'),
            data.get('state'),
            data.get('pin_code'),
            'active',
            datetime.now().isoformat(),
            'system'
        ))
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'party_uuid': party_uuid,
                'primary_name': data.get('primary_name'),
                'created_at': datetime.now().isoformat()
            }
        }), 201
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@party_matching_bp.route('/master-party/<party_uuid>', methods=['GET'])
@token_required
def get_master_party(party_uuid):
    """Get master party with aliases and merge history"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # Get master party
        cursor.execute('SELECT * FROM master_parties WHERE party_uuid = ?', (party_uuid,))
        party = dict(cursor.fetchone() or {})
        
        if not party:
            return jsonify({'success': False, 'error': 'Party not found'}), 404
        
        # Get aliases
        cursor.execute('SELECT * FROM party_aliases WHERE party_uuid = ?', (party_uuid,))
        aliases = [dict(row) for row in cursor.fetchall()]
        
        # Get merge history
        cursor.execute('''
            SELECT * FROM party_merges 
            WHERE target_party_uuid = ? AND merge_status = 'approved'
            ORDER BY merged_at DESC
        ''', (party_uuid,))
        merge_history = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'party': party,
                'aliases': [a['alias_name'] for a in aliases],
                'merge_history': merge_history
            }
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@party_matching_bp.route('/review-queue', methods=['GET'])
@require_admin
def get_review_queue():
    """Get pending merges for admin approval"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM party_review_queue
            WHERE review_status = 'pending'
            ORDER BY confidence_score DESC
        ''')
        
        reviews = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {'pending_reviews': reviews}
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@party_matching_bp.route('/approve-merge', methods=['POST'])
@require_admin
def approve_merge():
    """Approve a party merge"""
    try:
        data = request.get_json()
        match_id = data.get('match_id')
        merge_reason = data.get('merge_reason', 'Approved by admin')
        
        if not match_id:
            return jsonify({'success': False, 'error': 'match_id required'}), 400
        
        conn = get_db()
        cursor = conn.cursor()
        
        # Get match info
        cursor.execute('SELECT * FROM party_matching_history WHERE match_id = ?', (match_id,))
        match = dict(cursor.fetchone() or {})
        
        if not match:
            return jsonify({'success': False, 'error': 'Match not found'}), 404
        
        # Create merge record
        merge_id = None
        try:
            cursor.execute('''
                INSERT INTO party_merges 
                (source_party_uuid, target_party_uuid, confidence_score, merge_reason, merged_by, merged_at, merge_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                match['party1_uuid'],
                match['party2_uuid'],
                match['final_confidence_score'],
                merge_reason,
                'admin',
                datetime.now().isoformat(),
                'approved'
            ))
            merge_id = cursor.lastrowid
            
            # Move aliases from source to target
            cursor.execute('''
                UPDATE party_aliases 
                SET party_uuid = ?
                WHERE party_uuid = ?
            ''', (match['party2_uuid'], match['party1_uuid']))
            
            # Mark source as merged
            cursor.execute('''
                UPDATE master_parties 
                SET status = 'merged'
                WHERE party_uuid = ?
            ''', (match['party1_uuid'],))
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'merge_id': merge_id,
                'source_party_uuid': match['party1_uuid'],
                'target_party_uuid': match['party2_uuid'],
                'status': 'approved'
            }
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@party_matching_bp.route('/reverse-merge', methods=['POST'])
@require_admin
def reverse_merge():
    """Reverse/undo a party merge"""
    try:
        data = request.get_json()
        merge_id = data.get('merge_id')
        reason = data.get('reason', 'Reversed by admin')
        
        if not merge_id:
            return jsonify({'success': False, 'error': 'merge_id required'}), 400
        
        conn = get_db()
        cursor = conn.cursor()
        
        # Get merge info
        cursor.execute('SELECT * FROM party_merges WHERE id = ?', (merge_id,))
        merge = dict(cursor.fetchone() or {})
        
        if not merge:
            return jsonify({'success': False, 'error': 'Merge not found'}), 404
        
        if not merge.get('can_reverse'):
            return jsonify({'success': False, 'error': 'This merge cannot be reversed'}), 400
        
        try:
            # Move aliases back
            cursor.execute('''
                UPDATE party_aliases 
                SET party_uuid = ?
                WHERE party_uuid = ?
            ''', (merge['source_party_uuid'], merge['target_party_uuid']))
            
            # Mark source as active again
            cursor.execute('''
                UPDATE master_parties 
                SET status = 'active'
                WHERE party_uuid = ?
            ''', (merge['source_party_uuid'],))
            
            # Mark merge as reversed
            cursor.execute('''
                UPDATE party_merges 
                SET merge_status = 'reversed', reversed_at = ?, reversed_by = ?, reversal_reason = ?
                WHERE id = ?
            ''', (datetime.now().isoformat(), 'admin', reason, merge_id))
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'merge_id': merge_id,
                'status': 'reversed',
                'message': 'Merge reversed successfully'
            }
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

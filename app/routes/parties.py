from flask import Blueprint, request, jsonify
from sqlalchemy import func
from datetime import datetime, timezone

from app.db import db
from app.models import Distributor, Retailer
from app.routes.auth import require_jwt_auth

parties_bp = Blueprint('parties', __name__, url_prefix='/api/v1/parties')


def _current_user():
    return getattr(request, 'user', None)


# ========== DISTRIBUTOR ENDPOINTS ==========

@parties_bp.route('/distributors', methods=['GET'])
@require_jwt_auth
def get_distributors():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    offset = (page - 1) * limit
    search = (request.args.get('search') or '').strip()

    query = Distributor.query.filter_by(status='active')
    if search:
        pattern = f'%{search.lower()}%'
        query = query.filter(
            func.lower(Distributor.name).like(pattern) |
            func.lower(Distributor.gst_number).like(pattern)
        )

    total = query.count()
    distributors = query.order_by(Distributor.name).offset(offset).limit(limit).all()

    return jsonify({
        'success': True,
        'data': {
            'count': total,
            'page': page,
            'limit': limit,
            'results': [d.to_dict() for d in distributors],
        },
        'message': 'Distributors retrieved successfully',
    }), 200


@parties_bp.route('/distributors/<int:id>', methods=['GET'])
@require_jwt_auth
def get_distributor(id):
    distributor = db.session.get(Distributor, id)
    if not distributor:
        return jsonify({'success': False, 'data': None, 'message': 'Distributor not found'}), 404
    return jsonify({'success': True, 'data': distributor.to_dict(), 'message': 'Distributor retrieved successfully'}), 200


@parties_bp.route('/distributors', methods=['POST'])
@require_jwt_auth
def create_distributor():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'data': None, 'message': 'Name is required'}), 400

    gst_number = (data.get('gst_number') or '').strip() or None
    if gst_number:
        existing = Distributor.query.filter_by(gst_number=gst_number).first()
        if existing:
            return jsonify({'success': False, 'data': None, 'message': 'GST number already exists'}), 400

    distributor = Distributor(
        name=name,
        gst_number=gst_number,
        territory=data.get('territory'),
        city=data.get('city'),
        state=data.get('state'),
        pin_code=data.get('pin_code'),
        address=data.get('address'),
        credit_limit=data.get('credit_limit', 0.0) or 0.0,
        contact_person=data.get('contact_person'),
        phone=data.get('phone'),
        email=data.get('email'),
        status=data.get('status') or 'active',
        created_by=_current_user().get('user_id') if _current_user() else None,
    )

    try:
        db.session.add(distributor)
        db.session.commit()
        return jsonify({'success': True, 'data': distributor.to_dict(), 'message': 'Distributor created successfully'}), 201
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error creating distributor: {exc}'}), 500


@parties_bp.route('/distributors/<int:id>', methods=['PUT'])
@require_jwt_auth
def update_distributor(id):
    distributor = db.session.get(Distributor, id)
    if not distributor:
        return jsonify({'success': False, 'data': None, 'message': 'Distributor not found'}), 404

    data = request.get_json(silent=True) or {}
    if 'name' in data:
        distributor.name = (data.get('name') or distributor.name).strip()
    if 'gst_number' in data:
        gst_number = (data.get('gst_number') or '').strip() or None
        if gst_number and gst_number != distributor.gst_number:
            existing = Distributor.query.filter_by(gst_number=gst_number).first()
            if existing:
                return jsonify({'success': False, 'data': None, 'message': 'GST number already exists'}), 400
        distributor.gst_number = gst_number
    for field in ['territory', 'city', 'state', 'pin_code', 'address', 'credit_limit', 'contact_person', 'phone', 'email', 'status']:
        if field in data:
            setattr(distributor, field, data.get(field))

    distributor.updated_at = datetime.now(timezone.utc)
    try:
        db.session.commit()
        return jsonify({'success': True, 'data': distributor.to_dict(), 'message': 'Distributor updated successfully'}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error updating distributor: {exc}'}), 500


@parties_bp.route('/distributors/<int:id>', methods=['DELETE'])
@require_jwt_auth
def delete_distributor(id):
    distributor = db.session.get(Distributor, id)
    if not distributor:
        return jsonify({'success': False, 'data': None, 'message': 'Distributor not found'}), 404
    distributor.status = 'inactive'
    distributor.updated_at = datetime.now(timezone.utc)
    try:
        db.session.commit()
        return jsonify({'success': True, 'data': None, 'message': 'Distributor deleted successfully'}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error deleting distributor: {exc}'}), 500


# ========== RETAILER ENDPOINTS ==========

@parties_bp.route('/retailers', methods=['GET'])
@require_jwt_auth
def get_retailers():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    offset = (page - 1) * limit
    search = (request.args.get('search') or '').strip()
    distributor_id = request.args.get('distributor_id', type=int)

    query = Retailer.query.filter_by(status='active')
    if distributor_id:
        query = query.filter_by(distributor_id=distributor_id)
    if search:
        pattern = f'%{search.lower()}%'
        query = query.filter(
            func.lower(Retailer.name).like(pattern) |
            func.lower(Retailer.gst_number).like(pattern)
        )

    total = query.count()
    retailers = query.order_by(Retailer.name).offset(offset).limit(limit).all()
    return jsonify({'success': True, 'data': {'count': total, 'page': page, 'limit': limit, 'results': [r.to_dict() for r in retailers]} , 'message': 'Retailers retrieved successfully'}), 200


@parties_bp.route('/retailers/<int:id>', methods=['GET'])
@require_jwt_auth
def get_retailer(id):
    retailer = db.session.get(Retailer, id)
    if not retailer:
        return jsonify({'success': False, 'data': None, 'message': 'Retailer not found'}), 404
    return jsonify({'success': True, 'data': retailer.to_dict(), 'message': 'Retailer retrieved successfully'}), 200


@parties_bp.route('/retailers', methods=['POST'])
@require_jwt_auth
def create_retailer():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    distributor_id = data.get('distributor_id')
    if not name:
        return jsonify({'success': False, 'data': None, 'message': 'Name is required'}), 400
    if not distributor_id:
        return jsonify({'success': False, 'data': None, 'message': 'Distributor ID is required'}), 400

    distributor = db.session.get(Distributor, distributor_id)
    if not distributor:
        return jsonify({'success': False, 'data': None, 'message': 'Distributor not found'}), 404

    gst_number = (data.get('gst_number') or '').strip() or None
    if gst_number:
        existing = Retailer.query.filter_by(gst_number=gst_number).first()
        if existing:
            return jsonify({'success': False, 'data': None, 'message': 'GST number already exists'}), 400

    retailer = Retailer(
        name=name,
        gst_number=gst_number,
        distributor_id=distributor_id,
        territory=data.get('territory'),
        city=data.get('city'),
        state=data.get('state'),
        pin_code=data.get('pin_code'),
        address=data.get('address'),
        store_type=data.get('store_type'),
        contact_person=data.get('contact_person'),
        phone=data.get('phone'),
        email=data.get('email'),
        status=data.get('status') or 'active',
        created_by=_current_user().get('user_id') if _current_user() else None,
    )

    try:
        db.session.add(retailer)
        db.session.commit()
        return jsonify({'success': True, 'data': retailer.to_dict(), 'message': 'Retailer created successfully'}), 201
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error creating retailer: {exc}'}), 500


@parties_bp.route('/retailers/<int:id>', methods=['PUT'])
@require_jwt_auth
def update_retailer(id):
    retailer = db.session.get(Retailer, id)
    if not retailer:
        return jsonify({'success': False, 'data': None, 'message': 'Retailer not found'}), 404

    data = request.get_json(silent=True) or {}
    if 'name' in data:
        retailer.name = (data.get('name') or retailer.name).strip()
    if 'gst_number' in data:
        gst_number = (data.get('gst_number') or '').strip() or None
        if gst_number and gst_number != retailer.gst_number:
            existing = Retailer.query.filter_by(gst_number=gst_number).first()
            if existing:
                return jsonify({'success': False, 'data': None, 'message': 'GST number already exists'}), 400
        retailer.gst_number = gst_number
    if 'distributor_id' in data:
        distributor = db.session.get(Distributor, data['distributor_id'])
        if not distributor:
            return jsonify({'success': False, 'data': None, 'message': 'Distributor not found'}), 404
        retailer.distributor_id = data['distributor_id']
    for field in ['territory', 'city', 'state', 'pin_code', 'address', 'store_type', 'contact_person', 'phone', 'email', 'status']:
        if field in data:
            setattr(retailer, field, data.get(field))

    retailer.updated_at = datetime.now(timezone.utc)
    try:
        db.session.commit()
        return jsonify({'success': True, 'data': retailer.to_dict(), 'message': 'Retailer updated successfully'}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error updating retailer: {exc}'}), 500


@parties_bp.route('/retailers/<int:id>', methods=['DELETE'])
@require_jwt_auth
def delete_retailer(id):
    retailer = db.session.get(Retailer, id)
    if not retailer:
        return jsonify({'success': False, 'data': None, 'message': 'Retailer not found'}), 404
    retailer.status = 'inactive'
    retailer.updated_at = datetime.now(timezone.utc)
    try:
        db.session.commit()
        return jsonify({'success': True, 'data': None, 'message': 'Retailer deleted successfully'}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error deleting retailer: {exc}'}), 500

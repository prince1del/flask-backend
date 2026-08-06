from flask import Blueprint, request, jsonify
from sqlalchemy import func
from datetime import datetime, timezone

from app.db import db
from app.models import Distributor, Retailer
from app.routes.auth import get_workspace_id, require_jwt_auth


def _find_similar_active_party(model, workspace_id, name, phone, exclude_id=None):
    """
    Looks for an ACTIVE record with the same phone OR the same name
    (case-insensitive) in this workspace — used as a soft warning, NOT
    a hard block, since one person can genuinely run more than one
    firm sharing a phone number or a similar name. The caller decides
    whether to proceed after the user confirms.
    """
    name = (name or "").strip()
    phone = (phone or "").strip()
    if not name and not phone:
        return None

    query = model.query.filter_by(status="active", workspace_id=workspace_id)
    if exclude_id is not None:
        query = query.filter(model.id != exclude_id)

    conditions = []
    if phone:
        conditions.append(model.phone == phone)
    if name:
        conditions.append(func.lower(model.name) == name.lower())
    if not conditions:
        return None

    from sqlalchemy import or_
    return query.filter(or_(*conditions)).first()

parties_bp = Blueprint('parties', __name__, url_prefix='/api/v1/parties')


def _current_user():
    return getattr(request, 'user', None)


def _get_workspace_id():
    return get_workspace_id()


# ========== DISTRIBUTOR ENDPOINTS ==========

@parties_bp.route('/distributors', methods=['GET'])
@require_jwt_auth
def get_distributors():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    offset = (page - 1) * limit
    search = (request.args.get('search') or '').strip()

    workspace_id = _get_workspace_id()
    query = Distributor.query.filter_by(status='active', workspace_id=workspace_id)
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
    workspace_id = _get_workspace_id()
    distributor = Distributor.query.filter_by(id=id, workspace_id=workspace_id).first()
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
    workspace_id = _get_workspace_id()
    if gst_number:
        # GST numbers are genuinely unique per real firm by law — this
        # is a hard block. Scoped to ACTIVE records only: a
        # soft-deleted (inactive) distributor's old GST must not
        # permanently block reuse of that same GST for a new record.
        existing = Distributor.query.filter_by(
            gst_number=gst_number, workspace_id=workspace_id, status='active'
        ).first()
        if existing:
            return jsonify({'success': False, 'data': None, 'message': 'GST number already exists for an active distributor'}), 400

    # Soft warning only — one person can legitimately run more than
    # one firm sharing a phone number or a similar name. The frontend
    # shows this as a confirmation prompt; force_save=true re-submits
    # to actually proceed.
    if not data.get('force_save'):
        similar = _find_similar_active_party(
            Distributor, workspace_id, name, data.get('phone')
        )
        if similar:
            return jsonify({
                'success': False,
                'requires_confirmation': True,
                'data': {'existing': similar.to_dict()},
                'message': f"A distributor named '{similar.name}' with similar details already exists. Save anyway?",
            }), 409

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
        workspace_id=workspace_id,
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
    workspace_id = _get_workspace_id()
    distributor = Distributor.query.filter_by(id=id, workspace_id=workspace_id).first()
    if not distributor:
        return jsonify({'success': False, 'data': None, 'message': 'Distributor not found'}), 404

    data = request.get_json(silent=True) or {}
    if 'name' in data:
        distributor.name = (data.get('name') or distributor.name).strip()
    if 'gst_number' in data:
        gst_number = (data.get('gst_number') or '').strip() or None
        if gst_number and gst_number != distributor.gst_number:
            existing = Distributor.query.filter_by(
                gst_number=gst_number, workspace_id=workspace_id, status='active'
            ).first()
            if existing:
                return jsonify({'success': False, 'data': None, 'message': 'GST number already exists for an active distributor'}), 400
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
    workspace_id = _get_workspace_id()
    distributor = Distributor.query.filter_by(id=id, workspace_id=workspace_id).first()
    if not distributor:
        return jsonify({'success': False, 'data': None, 'message': 'Distributor not found'}), 404
    try:
        # Hard delete — permanently remove the contact (and ORM-cascaded children).
        db.session.delete(distributor)
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
    workspace_id = _get_workspace_id()

    query = Retailer.query.filter_by(status='active', workspace_id=workspace_id)
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
    workspace_id = _get_workspace_id()
    retailer = Retailer.query.filter_by(id=id, workspace_id=workspace_id).first()
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

    workspace_id = _get_workspace_id()
    distributor = Distributor.query.filter_by(id=distributor_id, workspace_id=workspace_id).first()
    if not distributor:
        return jsonify({'success': False, 'data': None, 'message': 'Distributor not found'}), 404

    gst_number = (data.get('gst_number') or '').strip() or None
    if gst_number:
        existing = Retailer.query.filter_by(
            gst_number=gst_number, workspace_id=workspace_id, status='active'
        ).first()
        if existing:
            return jsonify({'success': False, 'data': None, 'message': 'GST number already exists for an active retailer'}), 400

    if not data.get('force_save'):
        similar = _find_similar_active_party(
            Retailer, workspace_id, name, data.get('phone')
        )
        if similar:
            return jsonify({
                'success': False,
                'requires_confirmation': True,
                'data': {'existing': similar.to_dict()},
                'message': f"A retailer named '{similar.name}' with similar details already exists. Save anyway?",
            }), 409

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
        workspace_id=workspace_id,
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
    workspace_id = _get_workspace_id()
    retailer = Retailer.query.filter_by(id=id, workspace_id=workspace_id).first()
    if not retailer:
        return jsonify({'success': False, 'data': None, 'message': 'Retailer not found'}), 404

    data = request.get_json(silent=True) or {}
    if 'name' in data:
        retailer.name = (data.get('name') or retailer.name).strip()
    if 'gst_number' in data:
        gst_number = (data.get('gst_number') or '').strip() or None
        if gst_number and gst_number != retailer.gst_number:
            existing = Retailer.query.filter_by(
                gst_number=gst_number, workspace_id=workspace_id, status='active'
            ).first()
            if existing:
                return jsonify({'success': False, 'data': None, 'message': 'GST number already exists for an active retailer'}), 400
        retailer.gst_number = gst_number
    if 'distributor_id' in data:
        distributor = Distributor.query.filter_by(id=data['distributor_id'], workspace_id=workspace_id).first()
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
    workspace_id = _get_workspace_id()
    retailer = Retailer.query.filter_by(id=id, workspace_id=workspace_id).first()
    if not retailer:
        return jsonify({'success': False, 'data': None, 'message': 'Retailer not found'}), 404
    try:
        # Hard delete — permanently remove the contact.
        db.session.delete(retailer)
        db.session.commit()
        return jsonify({'success': True, 'data': None, 'message': 'Retailer deleted successfully'}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error deleting retailer: {exc}'}), 500

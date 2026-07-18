"""
Inventory Management API - Phase 4
Retail Operations Module

Endpoints:
- GET /api/inventory - List inventory with filters
- GET /api/inventory/{item_id}/history - Stock movement history
- POST /api/inventory/adjust - Add/remove stock
- PUT /api/inventory/{item_id}/min-max - Update stock thresholds
- GET /api/inventory/low-stock - Items below reorder level
"""

from flask import Blueprint, request, jsonify
from sqlalchemy import func, desc
from app.db import db
from app.models import Inventory, InventoryMovement
from app.routes.auth import get_workspace_id, require_jwt_auth
from app.business_platform import EventEngine, CacheManager, BusinessBrain, RulesEngine
from datetime import datetime, timezone

inventory_bp = Blueprint('inventory', __name__, url_prefix='/api/inventory')


def _get_workspace_id():
    return get_workspace_id()


# ========== INVENTORY 1: LIST INVENTORY ==========
@inventory_bp.route('', methods=['GET'])
@require_jwt_auth
def list_inventory():
    """List inventory items with pagination and filtering"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        category = request.args.get('category')
        warehouse = request.args.get('warehouse')
        status = request.args.get('status', 'active')
        search = request.args.get('search', '').strip()
        workspace_id = _get_workspace_id()
        
        query = Inventory.query.filter_by(workspace_id=workspace_id)
        
        if status:
            query = query.filter_by(status=status)
        if category:
            query = query.filter_by(category=category)
        if warehouse:
            query = query.filter_by(warehouse_id=warehouse)
        if search:
            pattern = f'%{search.lower()}%'
            query = query.filter(
                (func.lower(Inventory.item_code).like(pattern)) |
                (func.lower(Inventory.item_name).like(pattern))
            )
        
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        
        items = [item.to_dict() for item in pagination.items]
        
        return jsonify({
            'success': True,
            'data': items,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': pagination.total,
                'pages': pagination.pages
            },
            'filters': {
                'category': category,
                'warehouse': warehouse,
                'status': status,
                'search': search
            }
        }), 200
    
    except Exception as e:
        return jsonify({'success': False, 'data': None, 'message': f'Error listing inventory: {str(e)}'}), 500


# ========== INVENTORY 2: STOCK MOVEMENT HISTORY ==========
@inventory_bp.route('/<int:item_id>/history', methods=['GET'])
@require_jwt_auth
def get_inventory_history(item_id):
    """Get stock movement history for an item"""
    try:
        workspace_id = _get_workspace_id()
        item = (
            db.session.query(Inventory)
            .filter(Inventory.id == item_id, Inventory.workspace_id == workspace_id)
            .one_or_none()
        )
        if not item:
            return jsonify({'success': False, 'data': None, 'message': 'Inventory item not found'}), 404
        
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        movement_type = request.args.get('movement_type')  # receipt, issue, adjustment
        
        query = InventoryMovement.query.filter_by(inventory_id=item_id)
        
        if movement_type:
            query = query.filter_by(movement_type=movement_type)
        
        pagination = query.order_by(desc(InventoryMovement.created_at)).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        movements = [m.to_dict() for m in pagination.items]
        
        # Calculate statistics
        total_received = db.session.query(
            func.sum(InventoryMovement.quantity)
        ).filter_by(inventory_id=item_id, movement_type='receipt').scalar() or 0.0
        
        total_issued = db.session.query(
            func.sum(InventoryMovement.quantity)
        ).filter_by(inventory_id=item_id, movement_type='issue').scalar() or 0.0
        
        return jsonify({
            'success': True,
            'data': {
                'item': item.to_dict(),
                'movements': movements,
                'statistics': {
                    'total_received': round(float(total_received), 2),
                    'total_issued': round(float(total_issued), 2),
                    'current_stock': item.quantity_on_hand
                }
            },
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': pagination.total,
                'pages': pagination.pages
            }
        }), 200
    
    except Exception as e:
        return jsonify({'success': False, 'data': None, 'message': f'Error retrieving history: {str(e)}'}), 500


# ========== INVENTORY 3: ADJUST STOCK ==========
@inventory_bp.route('/adjust', methods=['POST'])
@require_jwt_auth
def adjust_inventory():
    """Adjust inventory (receipt, issue, or adjustment)"""
    data = request.get_json(silent=True) or {}
    
    item_id = data.get('item_id')
    quantity = data.get('quantity', 0)
    movement_type = data.get('movement_type', 'adjustment')  # receipt, issue, adjustment
    reason = data.get('reason', '')
    reference = data.get('reference_number', '')
    
    # Validate
    if not item_id or not quantity:
        return jsonify({'success': False, 'data': None, 'message': 'item_id and quantity required'}), 400
    
    if movement_type not in ['receipt', 'issue', 'adjustment']:
        return jsonify({'success': False, 'data': None, 'message': 'Invalid movement_type'}), 400
    
    try:
        workspace_id = _get_workspace_id()
        item = (
            db.session.query(Inventory)
            .filter(Inventory.id == item_id, Inventory.workspace_id == workspace_id)
            .one_or_none()
        )
        if not item:
            return jsonify({'success': False, 'data': None, 'message': 'Item not found'}), 404
        
        # For issues, check if sufficient stock available
        if movement_type == 'issue' and quantity > item.quantity_on_hand:
            return jsonify({'success': False, 'data': None, 'message': f'Insufficient stock. Available: {item.quantity_on_hand}'}), 400
        
        # Update quantity
        previous_qty = item.quantity_on_hand
        if movement_type == 'receipt':
            item.quantity_on_hand += quantity
            item.last_received = datetime.now(timezone.utc).date()
        elif movement_type == 'issue':
            item.quantity_on_hand -= quantity
            item.last_issued = datetime.now(timezone.utc).date()
        else:  # adjustment
            item.quantity_on_hand += quantity  # Can be positive or negative
        
        item.updated_at = datetime.now(timezone.utc)
        
        # Record movement
        movement = InventoryMovement(
            inventory_id=item_id,
            movement_type=movement_type,
            quantity=quantity,
            reason=reason,
            reference_number=reference,
            warehouse_to=item.warehouse_id if movement_type in ['receipt', 'adjustment'] else None,
            warehouse_from=item.warehouse_id if movement_type == 'issue' else None
        )
        
        db.session.add(movement)
        db.session.commit()
        
        # Emit event for chain reactions
        EventEngine.emit('inventory_adjusted', _get_workspace_id(), {
            'item_id': item_id,
            'warehouse_id': item.warehouse_id,
            'previous_qty': previous_qty,
            'new_qty': item.quantity_on_hand,
            'adjustment': quantity,
            'movement_type': movement_type,
            'reason': reason,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
        # Invalidate cache
        CacheManager.invalidate_inventory(_get_workspace_id(), item_id)
        
        return jsonify({
            'success': True,
            'data': {
                'item': item.to_dict(),
                'movement': movement.to_dict()
            },
            'message': f'Stock adjusted successfully'
        }), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error adjusting stock: {str(e)}'}), 500


# ========== INVENTORY 4: UPDATE MIN-MAX LEVELS ==========
@inventory_bp.route('/<int:item_id>/min-max', methods=['PUT'])
@require_jwt_auth
def update_min_max_levels(item_id):
    """Update reorder level and quantity for an item"""
    data = request.get_json(silent=True) or {}
    
    reorder_level = data.get('reorder_level')
    reorder_quantity = data.get('reorder_quantity')
    
    if reorder_level is None and reorder_quantity is None:
        return jsonify({'success': False, 'data': None, 'message': 'At least one of reorder_level or reorder_quantity required'}), 400
    
    try:
        workspace_id = _get_workspace_id()
        item = (
            db.session.query(Inventory)
            .filter(Inventory.id == item_id, Inventory.workspace_id == workspace_id)
            .one_or_none()
        )
        if not item:
            return jsonify({'success': False, 'data': None, 'message': 'Item not found'}), 404
        
        if reorder_level is not None:
            item.reorder_level = float(reorder_level)
        if reorder_quantity is not None:
            item.reorder_quantity = float(reorder_quantity)
        
        item.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'data': item.to_dict(),
            'message': 'Stock levels updated successfully'
        }), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': f'Error updating stock levels: {str(e)}'}), 500


# ========== INVENTORY 5: LOW STOCK ALERT ==========
@inventory_bp.route('/low-stock', methods=['GET'])
@require_jwt_auth
def get_low_stock_items():
    """Get items that are below reorder level"""
    try:
        workspace_id = _get_workspace_id()
        warehouse = request.args.get('warehouse')
        category = request.args.get('category')
        
        query = Inventory.query.filter(
            Inventory.workspace_id == workspace_id,
            Inventory.quantity_on_hand <= Inventory.reorder_level,
            Inventory.status == 'active'
        )
        
        if warehouse:
            query = query.filter_by(warehouse_id=warehouse)
        if category:
            query = query.filter_by(category=category)
        
        items = query.order_by(Inventory.quantity_on_hand).all()
        
        # Calculate suggestions
        suggestions = []
        for item in items:
            suggested_qty = item.reorder_quantity
            suggested_cost = suggested_qty * item.unit_cost
            
            suggestions.append({
                'item': item.to_dict(),
                'shortage': round(item.reorder_level - item.quantity_on_hand, 2),
                'suggested_order_qty': suggested_qty,
                'estimated_cost': round(suggested_cost, 2)
            })
        
        return jsonify({
            'success': True,
            'data': suggestions,
            'total_items': len(suggestions),
            'total_estimated_cost': round(sum(s['estimated_cost'] for s in suggestions), 2)
        }), 200
    
    except Exception as e:
        return jsonify({'success': False, 'data': None, 'message': f'Error retrieving low stock items: {str(e)}'}), 500

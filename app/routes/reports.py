from flask import Blueprint, request, jsonify
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta

from app.db import db
from app.models import SalesOrder, Invoice, InvoicePayment, Distributor, Retailer
from app.routes.auth import require_jwt_auth

reports_bp = Blueprint('reports', __name__, url_prefix='/api/v1/reports')


def _current_user():
    return getattr(request, 'user', None)


# ========== REPORT 1: SALES REPORT ==========
@reports_bp.route('/sales', methods=['GET'])
@require_jwt_auth
def get_sales_report():
    """Get sales report grouped by distributor/retailer/territory"""
    group_by = request.args.get('group_by', 'distributor')  # distributor, retailer, territory
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    # Build date filter
    date_filter = []
    if start_date:
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d').date()
            date_filter.append(SalesOrder.order_date >= start)
        except ValueError:
            return jsonify({'success': False, 'data': None, 'message': 'Invalid start_date format (use YYYY-MM-DD)'}), 400
    
    if end_date:
        try:
            end = datetime.strptime(end_date, '%Y-%m-%d').date()
            date_filter.append(SalesOrder.order_date <= end)
        except ValueError:
            return jsonify({'success': False, 'data': None, 'message': 'Invalid end_date format (use YYYY-MM-DD)'}), 400
    
    try:
        if group_by == 'distributor':
            results = db.session.query(
                SalesOrder.distributor_id,
                Distributor.name.label('distributor_name'),
                func.sum(SalesOrder.net_amount).label('total_amount'),
                func.count(SalesOrder.id).label('order_count'),
                func.avg(SalesOrder.net_amount).label('avg_amount')
            ).join(Distributor, SalesOrder.distributor_id == Distributor.id)
            
            if date_filter:
                for f in date_filter:
                    results = results.filter(f)
            
            results = results.group_by(SalesOrder.distributor_id, Distributor.name).all()
            
            data = [
                {
                    'id': r[0],
                    'name': r[1],
                    'total_amount': float(r[2]) if r[2] else 0.0,
                    'order_count': r[3],
                    'avg_amount': float(r[4]) if r[4] else 0.0
                }
                for r in results
            ]
        
        elif group_by == 'retailer':
            results = db.session.query(
                SalesOrder.retailer_id,
                Retailer.name.label('retailer_name'),
                func.sum(SalesOrder.net_amount).label('total_amount'),
                func.count(SalesOrder.id).label('order_count'),
                func.avg(SalesOrder.net_amount).label('avg_amount')
            ).join(Retailer, SalesOrder.retailer_id == Retailer.id)
            
            if date_filter:
                for f in date_filter:
                    results = results.filter(f)
            
            results = results.group_by(SalesOrder.retailer_id, Retailer.name).all()
            
            data = [
                {
                    'id': r[0],
                    'name': r[1],
                    'total_amount': float(r[2]) if r[2] else 0.0,
                    'order_count': r[3],
                    'avg_amount': float(r[4]) if r[4] else 0.0
                }
                for r in results
            ]
        
        elif group_by == 'territory':
            results = db.session.query(
                Distributor.territory,
                func.sum(SalesOrder.net_amount).label('total_amount'),
                func.count(SalesOrder.id).label('order_count'),
                func.avg(SalesOrder.net_amount).label('avg_amount')
            ).join(Distributor, SalesOrder.distributor_id == Distributor.id)
            
            if date_filter:
                for f in date_filter:
                    results = results.filter(f)
            
            results = results.group_by(Distributor.territory).all()
            
            data = [
                {
                    'territory': r[0],
                    'total_amount': float(r[1]) if r[1] else 0.0,
                    'order_count': r[2],
                    'avg_amount': float(r[3]) if r[3] else 0.0
                }
                for r in results
            ]
        
        else:
            return jsonify({'success': False, 'data': None, 'message': 'Invalid group_by parameter. Use: distributor, retailer, or territory'}), 400
        
        return jsonify({
            'success': True,
            'data': data,
            'group_by': group_by,
            'filters': {'start_date': start_date, 'end_date': end_date}
        }), 200
    
    except Exception as e:
        return jsonify({'success': False, 'data': None, 'message': f'Error generating report: {str(e)}'}), 500


# ========== REPORT 2: TRENDS REPORT ==========
@reports_bp.route('/trends', methods=['GET'])
@require_jwt_auth
def get_trends_report():
    """Calculate month-over-month growth trends"""
    months_back = request.args.get('months_back', 12, type=int)
    
    if months_back < 1 or months_back > 24:
        return jsonify({'success': False, 'data': None, 'message': 'months_back must be between 1 and 24'}), 400
    
    try:
        today = datetime.now(timezone.utc).date()
        trends = []
        
        for i in range(months_back, 0, -1):
            month_date = today - relativedelta(months=i)
            month_start = month_date.replace(day=1)
            month_end = (month_start + relativedelta(months=1)) - timedelta(days=1)
            
            # Get current month sales
            current_month_sales = db.session.query(
                func.sum(SalesOrder.net_amount).label('total')
            ).filter(
                SalesOrder.order_date >= month_start,
                SalesOrder.order_date <= month_end
            ).first()
            
            current_total = float(current_month_sales[0]) if current_month_sales[0] else 0.0
            
            # Get previous month sales for comparison
            prev_month_start = (month_start - relativedelta(months=1)).replace(day=1)
            prev_month_end = month_start - timedelta(days=1)
            
            prev_month_sales = db.session.query(
                func.sum(SalesOrder.net_amount).label('total')
            ).filter(
                SalesOrder.order_date >= prev_month_start,
                SalesOrder.order_date <= prev_month_end
            ).first()
            
            prev_total = float(prev_month_sales[0]) if prev_month_sales[0] else 0.0
            
            # Calculate growth percentage
            if prev_total > 0:
                growth_percent = ((current_total - prev_total) / prev_total) * 100
            else:
                growth_percent = 100.0 if current_total > 0 else 0.0
            
            trends.append({
                'month': month_date.strftime('%Y-%m'),
                'current_sales': current_total,
                'previous_sales': prev_total,
                'growth_percent': round(growth_percent, 2),
                'trend': 'up' if growth_percent >= 0 else 'down'
            })
        
        return jsonify({
            'success': True,
            'data': trends,
            'months_back': months_back
        }), 200
    
    except Exception as e:
        return jsonify({'success': False, 'data': None, 'message': f'Error generating trends: {str(e)}'}), 500


# ========== REPORT 3: TERRITORY PERFORMANCE ==========
@reports_bp.route('/territory-performance', methods=['GET'])
@require_jwt_auth
def get_territory_performance():
    """Rank territories by sales performance"""
    try:
        # Get total sales by territory
        territory_sales = db.session.query(
            Distributor.territory,
            func.sum(SalesOrder.net_amount).label('total_sales'),
            func.count(SalesOrder.id).label('order_count')
        ).join(Distributor, SalesOrder.distributor_id == Distributor.id).group_by(
            Distributor.territory
        ).order_by(func.sum(SalesOrder.net_amount).desc()).all()
        
        # Calculate total for percentage
        grand_total = sum(float(r[1]) if r[1] else 0.0 for r in territory_sales)
        
        # Build response with rankings
        data = []
        for rank, (territory, total_sales, order_count) in enumerate(territory_sales, 1):
            total_sales_float = float(total_sales) if total_sales else 0.0
            percent_of_total = ((total_sales_float / grand_total) * 100) if grand_total > 0 else 0.0
            
            data.append({
                'rank': rank,
                'territory': territory,
                'total_sales': total_sales_float,
                'order_count': order_count,
                'percent_of_total': round(percent_of_total, 2)
            })
        
        return jsonify({
            'success': True,
            'data': data,
            'grand_total': round(grand_total, 2)
        }), 200
    
    except Exception as e:
        return jsonify({'success': False, 'data': None, 'message': f'Error generating territory report: {str(e)}'}), 500


# ========== REPORT 4: DISTRIBUTOR SALES DETAIL ==========
@reports_bp.route('/distributor-sales/<int:distributor_id>', methods=['GET'])
@require_jwt_auth
def get_distributor_sales_detail(distributor_id):
    """Detailed sales breakdown for a single distributor"""
    try:
        distributor = db.session.get(Distributor, distributor_id)
        if not distributor:
            return jsonify({'success': False, 'data': None, 'message': 'Distributor not found'}), 404
        
        # Get all sales orders for this distributor
        sales_orders = SalesOrder.query.filter_by(distributor_id=distributor_id).all()
        
        total_orders = len(sales_orders)
        total_sales = sum(float(so.net_amount) if so.net_amount else 0.0 for so in sales_orders)
        
        # Get invoices
        invoices = db.session.query(
            func.sum(Invoice.total_amount).label('total_invoiced'),
            func.count(Invoice.id).label('invoice_count')
        ).join(SalesOrder).filter(SalesOrder.distributor_id == distributor_id).first()
        
        total_invoiced = float(invoices[0]) if invoices[0] else 0.0
        invoice_count = invoices[1] if invoices[1] else 0
        
        # Get payments
        payments = db.session.query(
            func.sum(InvoicePayment.amount_paid).label('total_paid')
        ).join(Invoice, InvoicePayment.invoice_id == Invoice.id).join(
            SalesOrder, Invoice.so_id == SalesOrder.id
        ).filter(SalesOrder.distributor_id == distributor_id).first()
        
        total_paid = float(payments[0]) if payments[0] else 0.0
        outstanding = total_invoiced - total_paid
        
        data = {
            'distributor_id': distributor_id,
            'distributor_name': distributor.name,
            'total_orders': total_orders,
            'total_sales': round(total_sales, 2),
            'total_invoiced': round(total_invoiced, 2),
            'total_paid': round(total_paid, 2),
            'outstanding_amount': round(outstanding, 2),
            'invoice_count': invoice_count
        }
        
        return jsonify({
            'success': True,
            'data': data
        }), 200
    
    except Exception as e:
        return jsonify({'success': False, 'data': None, 'message': f'Error getting distributor details: {str(e)}'}), 500


# ========== REPORT 5: CUSTOM REPORTS ==========
@reports_bp.route('/custom', methods=['POST'])
@require_jwt_auth
def create_custom_report():
    """Dynamic query builder for custom reports"""
    data = request.get_json(silent=True) or {}
    
    group_by = data.get('group_by', 'distributor')  # distributor, retailer, territory
    filter_by = data.get('filter_by')  # Optional: distributor_id, retailer_id, territory
    filter_value = data.get('filter_value')
    start_date = data.get('start_date')
    end_date = data.get('end_date')
    export_format = data.get('export_format', 'json')  # json, csv
    
    if export_format not in ['json', 'csv']:
        return jsonify({'success': False, 'data': None, 'message': 'export_format must be json or csv'}), 400
    
    try:
        # Build query
        query = db.session.query(
            SalesOrder.distributor_id,
            SalesOrder.retailer_id,
            Distributor.name.label('distributor_name'),
            Distributor.territory,
            Retailer.name.label('retailer_name'),
            func.sum(SalesOrder.net_amount).label('total_sales'),
            func.count(SalesOrder.id).label('order_count'),
            func.avg(SalesOrder.net_amount).label('avg_order_value')
        ).join(Distributor, SalesOrder.distributor_id == Distributor.id).join(
            Retailer, SalesOrder.retailer_id == Retailer.id
        )
        
        # Apply filters
        if start_date:
            try:
                start = datetime.strptime(start_date, '%Y-%m-%d').date()
                query = query.filter(SalesOrder.order_date >= start)
            except ValueError:
                return jsonify({'success': False, 'data': None, 'message': 'Invalid start_date format'}), 400
        
        if end_date:
            try:
                end = datetime.strptime(end_date, '%Y-%m-%d').date()
                query = query.filter(SalesOrder.order_date <= end)
            except ValueError:
                return jsonify({'success': False, 'data': None, 'message': 'Invalid end_date format'}), 400
        
        if filter_by == 'distributor_id' and filter_value:
            query = query.filter(SalesOrder.distributor_id == int(filter_value))
        elif filter_by == 'retailer_id' and filter_value:
            query = query.filter(SalesOrder.retailer_id == int(filter_value))
        elif filter_by == 'territory' and filter_value:
            query = query.filter(Distributor.territory == filter_value)
        
        # Apply grouping
        if group_by == 'distributor':
            query = query.group_by(SalesOrder.distributor_id, Distributor.name, Distributor.territory)
        elif group_by == 'retailer':
            query = query.group_by(SalesOrder.retailer_id, Retailer.name)
        elif group_by == 'territory':
            query = query.group_by(Distributor.territory)
        
        results = query.all()
        
        # Format results
        formatted_data = []
        for row in results:
            formatted_data.append({
                'distributor_id': row[0],
                'retailer_id': row[1],
                'distributor_name': row[2],
                'territory': row[3],
                'retailer_name': row[4],
                'total_sales': float(row[5]) if row[5] else 0.0,
                'order_count': row[6],
                'avg_order_value': float(row[7]) if row[7] else 0.0
            })
        
        if export_format == 'csv':
            # Return CSV format
            import io
            csv_buffer = io.StringIO()
            if formatted_data:
                import csv as csv_module
                fieldnames = list(formatted_data[0].keys())
                writer = csv_module.DictWriter(csv_buffer, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(formatted_data)
            
            return {
                'success': True,
                'data': csv_buffer.getvalue(),
                'format': 'csv'
            }, 200, {'Content-Type': 'text/csv', 'Content-Disposition': 'attachment;filename=report.csv'}
        
        else:  # JSON
            return jsonify({
                'success': True,
                'data': formatted_data,
                'filters': {
                    'group_by': group_by,
                    'filter_by': filter_by,
                    'filter_value': filter_value,
                    'start_date': start_date,
                    'end_date': end_date
                },
                'record_count': len(formatted_data)
            }), 200
    
    except ValueError as e:
        return jsonify({'success': False, 'data': None, 'message': f'Invalid parameter value: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'success': False, 'data': None, 'message': f'Error generating custom report: {str(e)}'}), 500

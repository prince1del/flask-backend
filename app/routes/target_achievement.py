from flask import Blueprint, current_app, jsonify, request
import sqlite3
from datetime import datetime
import json

from app.routes.auth import require_jwt_auth, get_workspace_id
from app.services.sales_achievement_parser import parse_sales_achievement_excel
from app.fiscal_year import fiscal_year_sort_key, normalize_fiscal_year
from centralized_db_system.db import CentralizedDB

# Create blueprint
target_achievement_bp = Blueprint('target_achievement', __name__, url_prefix='/api/v1/target-achievement')

def get_db():
    conn = sqlite3.connect(current_app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    return conn

def _cdb() -> CentralizedDB:
    return CentralizedDB(current_app.config['DATABASE_PATH'])

def _year_row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    raw_label = data.get("financial_year") or data.get("year") or ""
    display = normalize_fiscal_year(raw_label) or raw_label
    data["year"] = display
    data["financial_year"] = display
    data["display_year"] = display
    data["target"] = data.get("target") if data.get("target") is not None else data.get("target_amount")
    data["achievement"] = (
        data.get("achievement")
        if data.get("achievement") is not None
        else data.get("achievement_amount")
    )
    return data

def _find_year_by_normalized(conn: sqlite3.Connection, workspace_id: str, normalized_year: str) -> dict | None:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM target_achievement_years WHERE workspace_id = ?",
        (workspace_id,),
    )
    for row in cursor.fetchall():
        data = _year_row_to_dict(row)
        if data.get("display_year") == normalized_year:
            return data
    return None

def _year_rank_for_dedupe(year: dict, child_counts: dict[int, int] | None = None) -> tuple:
    """Prefer FY row with linked breakup/upload data, then target, then canonical label, then lower id."""
    child_counts = child_counts or {}
    yid = int(year.get("id") or 0)
    raw = year.get("financial_year") or year.get("year") or year.get("display_year") or ""
    canonical = normalize_fiscal_year(raw) == raw if raw else False
    target_val = float(year.get("target") or year.get("target_amount") or 0)
    return (child_counts.get(yid, 0), target_val, 1 if canonical else 0, -yid)


def _dedupe_years_by_display(
    years: list[dict], child_counts: dict[int, int] | None = None
) -> list[dict]:
    """One row per canonical FY label; keep row with most child data."""
    by_label: dict[str, dict] = {}
    for year in years:
        label = year.get("display_year") or year.get("financial_year") or year.get("year") or ""
        if not label:
            continue
        prev = by_label.get(label)
        if not prev or _year_rank_for_dedupe(year, child_counts) > _year_rank_for_dedupe(
            prev, child_counts
        ):
            by_label[label] = year
    deduped = list(by_label.values())
    deduped.sort(key=lambda y: fiscal_year_sort_key(y.get("display_year")))
    return deduped

def _get_year_or_404(year_id: int, workspace_id: str) -> dict | None:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM target_achievement_years WHERE id = ? AND workspace_id = ?',
        (year_id, workspace_id),
    )
    row = cursor.fetchone()
    conn.close()
    return _year_row_to_dict(row) if row else None

# ==================== ROUTES ====================

@target_achievement_bp.route('/years', methods=['GET'])
@require_jwt_auth
def get_years():
    """Get all fiscal years for the current workspace"""
    try:
        workspace_id = get_workspace_id()
        _cdb().merge_duplicate_fiscal_years(workspace_id)
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM target_achievement_years WHERE workspace_id = ? ORDER BY COALESCE(financial_year, year) ASC', (workspace_id,))
        years = [_year_row_to_dict(row) for row in cursor.fetchall()]
        child_counts: dict[int, int] = {}
        try:
            for row in cursor.execute(
                """
                SELECT financial_year_id, COUNT(*) FROM target_achievement_breakup
                GROUP BY financial_year_id
                """
            ).fetchall():
                child_counts[int(row[0])] = int(row[1])
        except sqlite3.OperationalError:
            pass
        years = _dedupe_years_by_display(years, child_counts)
        conn.close()

        return jsonify({'success': True, 'data': {'years': years}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/fy-overview', methods=['GET'])
@require_jwt_auth
def get_fy_overview():
    """All fiscal years with target, achievement, and % (lakhs) for dashboard cards."""
    try:
        workspace_id = get_workspace_id()
        db = _cdb()
        db.merge_duplicate_fiscal_years(workspace_id)
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM target_achievement_years WHERE workspace_id = ? ORDER BY COALESCE(financial_year, year) ASC',
            (workspace_id,),
        )
        years = [_year_row_to_dict(row) for row in cursor.fetchall()]
        child_counts: dict[int, int] = {}
        try:
            for row in cursor.execute(
                """
                SELECT financial_year_id, COUNT(*) FROM target_achievement_breakup
                GROUP BY financial_year_id
                """
            ).fetchall():
                child_counts[int(row[0])] = int(row[1])
        except sqlite3.OperationalError:
            pass
        years = _dedupe_years_by_display(years, child_counts)
        conn.close()

        rows = []
        for year in years:
            year_id = int(year["id"])
            fy_label = year.get("display_year") or year.get("financial_year") or year.get("year") or ""
            try:
                summary = db.build_fy_achievement_summary(workspace_id, year_id, fy_label)
            except Exception:
                target = float(year.get("target") or year.get("target_amount") or 0)
                summary = {
                    "target_lakhs": target,
                    "active_achievement": 0.0,
                    "percentage": 0.0,
                }
            rows.append(
                {
                    "id": year_id,
                    "fy": fy_label,
                    "target": summary.get("target_lakhs") or 0,
                    "achievement": summary.get("active_achievement") or 0,
                    "percentage": summary.get("percentage") or 0,
                    "unit": "lakhs",
                }
            )

        return jsonify({'success': True, 'data': {'rows': rows, 'unit': 'lakhs'}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years', methods=['POST'])
@require_jwt_auth
def create_year():
    """Create new fiscal year for the current workspace"""
    try:
        data = request.get_json()
        year = normalize_fiscal_year(data.get('year') or data.get('financial_year'))
        target = data.get('target')
        unit = (data.get('unit') or 'lakhs').lower()

        if not year or target is None:
            return jsonify({'success': False, 'error': 'Year and target required'}), 400

        workspace_id = get_workspace_id()
        _cdb().merge_duplicate_fiscal_years(workspace_id)
        conn = get_db()
        cursor = conn.cursor()
        existing = _find_year_by_normalized(conn, workspace_id, year)
        cols = {r[1] for r in cursor.execute('PRAGMA table_info(target_achievement_years)').fetchall()}
        now = datetime.now().isoformat()

        if existing:
            year_id = int(existing["id"])
            sets = []
            params: list = []
            if 'target_amount' in cols:
                sets.append('target_amount = ?')
                params.append(target)
            if 'target' in cols:
                sets.append('target = ?')
                params.append(target)
            if 'year' in cols:
                sets.append('year = ?')
                params.append(year)
            if 'financial_year' in cols:
                sets.append('financial_year = ?')
                params.append(year)
            if 'updated_at' in cols:
                sets.append('updated_at = ?')
                params.append(now)
            if sets:
                params.extend([year_id, workspace_id])
                cursor.execute(
                    f"UPDATE target_achievement_years SET {', '.join(sets)} WHERE id = ? AND workspace_id = ?",
                    tuple(params),
                )
                conn.commit()
            conn.close()
            return jsonify(
                {
                    'success': True,
                    'data': {
                        'year_id': year_id,
                        'year': year,
                        'target': target,
                        'unit': unit,
                        'updated_existing': True,
                    },
                }
            ), 200

        insert_cols = ['workspace_id']
        insert_vals: list = [workspace_id]
        if 'year' in cols:
            insert_cols.append('year')
            insert_vals.append(year)
        if 'financial_year' in cols:
            insert_cols.append('financial_year')
            insert_vals.append(year)
        if 'target_amount' in cols:
            insert_cols.append('target_amount')
            insert_vals.append(target)
        if 'target' in cols:
            insert_cols.append('target')
            insert_vals.append(target)
        if 'created_at' in cols:
            insert_cols.append('created_at')
            insert_vals.append(now)

        placeholders = ', '.join('?' for _ in insert_cols)
        cursor.execute(
            f"INSERT INTO target_achievement_years ({', '.join(insert_cols)}) VALUES ({placeholders})",
            tuple(insert_vals),
        )
        conn.commit()
        year_id = cursor.lastrowid
        conn.close()

        return jsonify({'success': True, 'data': {'year_id': year_id, 'year': year, 'target': target, 'unit': unit}}), 201
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years/<int:year_id>', methods=['PUT'])
@require_jwt_auth
def update_year(year_id):
    """Update fiscal year target (workspace-scoped)"""
    try:
        data = request.get_json()
        target = data.get('target')

        if target is None:
            return jsonify({'success': False, 'error': 'Target required'}), 400

        workspace_id = get_workspace_id()
        conn = get_db()
        cursor = conn.cursor()
        cols = {r[1] for r in cursor.execute('PRAGMA table_info(target_achievement_years)').fetchall()}
        now = datetime.now().isoformat()
        sets = []
        params: list = []
        if 'target_amount' in cols:
            sets.append('target_amount = ?')
            params.append(target)
        if 'target' in cols:
            sets.append('target = ?')
            params.append(target)
        if 'updated_at' in cols:
            sets.append('updated_at = ?')
            params.append(now)
        if not sets:
            conn.close()
            return jsonify({'success': False, 'error': 'No target column in database'}), 500
        params.extend([year_id, workspace_id])
        cursor.execute(
            f"UPDATE target_achievement_years SET {', '.join(sets)} WHERE id = ? AND workspace_id = ?",
            tuple(params),
        )
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'success': False, 'error': 'Year not found'}), 404
        conn.commit()
        conn.close()

        return jsonify({'success': True, 'data': {'year_id': year_id, 'target': target, 'unit': 'lakhs'}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years/<int:year_id>/summary', methods=['GET'])
@require_jwt_auth
def get_summary(year_id):
    """Get target vs achievement summary (workspace-scoped)"""
    try:
        workspace_id = get_workspace_id()
        conn = get_db()
        cursor = conn.cursor()

        # Get year (workspace enforced)
        cursor.execute('SELECT * FROM target_achievement_years WHERE id = ? AND workspace_id = ?', (year_id, workspace_id))
        year = _year_row_to_dict(cursor.fetchone() or {})

        if not year:
            return jsonify({'success': False, 'error': 'Year not found'}), 404

        achievement = sum(
            row.get("achievement_lakhs") or 0
            for row in _cdb().list_target_distributor_breakup(workspace_id, year_id)
        )
        if not achievement:
            cursor.execute('''
                SELECT SUM(COALESCE(calculated_total, amount, 0)) as total
                FROM target_achievement_uploads
                WHERE financial_year_id = ? AND workspace_id = ?
            ''', (year_id, workspace_id))
            result = cursor.fetchone()
            achievement = (result['total'] if result and result['total'] is not None else 0)

        conn.close()

        target = year.get('target', 0) or year.get('target_amount', 0) or 0
        percentage = (achievement / target * 100) if target > 0 else 0

        return jsonify({'success': True, 'data': {'year_id': year_id, 'target': target, 'achievement': achievement, 'percentage': round(percentage, 2), 'unit': 'lakhs'}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years/<int:year_id>/distributor-targets', methods=['GET'])
@require_jwt_auth
def get_distributor_targets(year_id):
    """Distributor target list only — no achievement sync (targets workspace)."""
    try:
        workspace_id = get_workspace_id()
        year = _get_year_or_404(year_id, workspace_id)
        if not year:
            return jsonify({'success': False, 'error': 'Year not found'}), 404
        breakup = _cdb().list_target_distributor_breakup(workspace_id, year_id)
        rows = [
            {
                "distributor_name": r.get("distributor_name"),
                "display_label": r.get("display_label") or r.get("distributor_name"),
                "target_lakhs": r.get("target_lakhs") or 0,
            }
            for r in breakup
            if float(r.get("target_lakhs") or 0) > 0
        ]
        fy_label = year.get("display_year") or year.get("financial_year") or year.get("year") or ""
        target = year.get("target") or year.get("target_amount") or 0
        return jsonify(
            {
                'success': True,
                'data': {
                    'fy_label': fy_label,
                    'target_lakhs': target,
                    'rows': rows,
                    'unit': 'lakhs',
                },
            }
        ), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years/<int:year_id>/breakup', methods=['GET'])
@require_jwt_auth
def get_breakup(year_id):
    """Get distributor-wise breakup with targets (workspace-scoped, lakhs)."""
    try:
        workspace_id = get_workspace_id()
        year = _get_year_or_404(year_id, workspace_id)
        if not year:
            return jsonify({'success': False, 'error': 'Year not found'}), 404
        db = _cdb()
        fy_label = year.get("display_year") or year.get("financial_year") or year.get("year") or ""
        summary = db.build_fy_achievement_summary(workspace_id, year_id, fy_label)
        breakup = db.list_target_distributor_breakup(workspace_id, year_id)
        category_matrix = db.get_category_breakup_matrix(workspace_id, year_id)
        return jsonify(
            {
                'success': True,
                'data': {
                    'breakup': breakup,
                    'summary': summary,
                    'category_matrix': category_matrix,
                    'has_category_detail': category_matrix.get('has_data', False),
                    'unit': 'lakhs',
                    'fy_label': fy_label,
                },
            }
        ), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years/<int:year_id>/category-breakup', methods=['GET'])
@require_jwt_auth
def get_category_breakup(year_id):
    """Distributor × category achievement matrix for detail modal."""
    try:
        workspace_id = get_workspace_id()
        year = _get_year_or_404(year_id, workspace_id)
        if not year:
            return jsonify({'success': False, 'error': 'Year not found'}), 404
        matrix = _cdb().get_category_breakup_matrix(workspace_id, year_id)
        fy_label = year.get("display_year") or year.get("financial_year") or year.get("year") or ""
        matrix["fy_label"] = fy_label
        if not matrix.get("has_data"):
            return jsonify(
                {'success': False, 'error': 'No category-wise data for this fiscal year. Upload sales Excel with category rows.'}
            ), 404
        return jsonify({'success': True, 'data': matrix}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years/<int:year_id>/manual-fy-achievement', methods=['POST'])
@require_jwt_auth
def set_manual_fy_achievement(year_id):
    """Set overall FY achievement manually (lakhs)."""
    try:
        data = request.get_json(silent=True) or {}
        amount = data.get('achievement_lakhs', data.get('amount'))
        if amount is None:
            return jsonify({'success': False, 'error': 'achievement_lakhs required'}), 400
        workspace_id = get_workspace_id()
        if not _get_year_or_404(year_id, workspace_id):
            return jsonify({'success': False, 'error': 'Year not found'}), 404
        _cdb().set_fy_manual_achievement(workspace_id, year_id, float(amount))
        return jsonify({'success': True, 'data': {'achievement_lakhs': float(amount)}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years/<int:year_id>/distributor-target', methods=['POST'])
@require_jwt_auth
def set_distributor_target(year_id):
    """Set distributor-wise FY target manually (lakhs)."""
    try:
        data = request.get_json(silent=True) or {}
        distributor_name = (data.get('distributor_name') or '').strip()
        target = data.get('target_lakhs', data.get('target'))
        nick = (data.get('nick') or '').strip() or None
        if not distributor_name or target is None:
            return jsonify({'success': False, 'error': 'distributor_name and target_lakhs required'}), 400

        workspace_id = get_workspace_id()
        if not _get_year_or_404(year_id, workspace_id):
            return jsonify({'success': False, 'error': 'Year not found'}), 404

        _cdb().set_target_distributor_target_lakhs(
            workspace_id=workspace_id,
            financial_year_id=year_id,
            distributor_name=distributor_name,
            target_lakhs=float(target),
            nick=nick,
        )
        return jsonify({'success': True, 'data': {'distributor_name': distributor_name, 'target_lakhs': float(target)}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years/<int:year_id>/achievement', methods=['DELETE'])
@require_jwt_auth
def clear_fy_achievement(year_id):
    """Clear all achievement for a fiscal year (Excel, CI, manual, category breakup)."""
    try:
        workspace_id = get_workspace_id()
        year = _get_year_or_404(year_id, workspace_id)
        if not year:
            return jsonify({'success': False, 'error': 'Year not found'}), 404
        result = _cdb().clear_fy_achievement(workspace_id, year_id)
        fy_label = year.get('display_year') or year.get('financial_year') or year.get('year') or ''
        return jsonify(
            {
                'success': True,
                'data': {
                    'fy_label': fy_label,
                    'achievement_lakhs': result.get('achievement_lakhs', 0),
                },
            }
        ), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years/<int:year_id>/targets', methods=['DELETE'])
@require_jwt_auth
def clear_fy_targets(year_id):
    """Clear FY and distributor targets for a fiscal year; keeps achievement."""
    try:
        workspace_id = get_workspace_id()
        year = _get_year_or_404(year_id, workspace_id)
        if not year:
            return jsonify({'success': False, 'error': 'Year not found'}), 404
        _cdb().clear_fy_targets(workspace_id, year_id)
        fy_label = year.get('display_year') or year.get('financial_year') or year.get('year') or ''
        return jsonify({'success': True, 'data': {'fy_label': fy_label}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years/<int:year_id>/upload-sales-excel', methods=['POST'])
@require_jwt_auth
def upload_sales_excel(year_id):
    """Upload pivot sales Excel — imports distributor achievement in lakhs."""
    try:
        workspace_id = get_workspace_id()
        if not _get_year_or_404(year_id, workspace_id):
            return jsonify({'success': False, 'error': 'Year not found'}), 404

        upload = request.files.get('file')
        if not upload:
            return jsonify({'success': False, 'error': 'Excel file required (field name: file)'}), 400

        parsed = parse_sales_achievement_excel(upload.read(), upload.filename or 'sales.xlsx')
        db = _cdb()
        file_kind = parsed.get('file_kind') or 'achievement'
        if file_kind == 'budget':
            imported = db.import_sales_excel_targets(workspace_id, year_id, parsed)
        else:
            imported = db.import_sales_excel_achievement(workspace_id, year_id, parsed)

        try:
            db.save_upload_record(
                year_id,
                parsed.get('filename') or upload.filename or 'sales.xlsx',
                'xlsx',
                imported.get('distributor_count') or 0,
                imported.get('total_achievement_lakhs') or imported.get('total_target_lakhs') or 0,
                None,
            )
        except Exception:
            pass

        response_data = {
            'unit': 'lakhs',
            'file_kind': file_kind,
            'distributor_count': imported.get('distributor_count'),
            'financial_year_hint': parsed.get('financial_year_hint'),
            'distributors': parsed.get('distributors'),
        }
        if file_kind == 'budget':
            response_data['total_target_lakhs'] = imported.get('total_target_lakhs')
        else:
            response_data['total_achievement_lakhs'] = imported.get('total_achievement_lakhs')
            response_data['category_row_count'] = imported.get('category_row_count')
            response_data['has_category_detail'] = imported.get('has_category_detail')
            response_data['categories'] = list(
                {
                    c.get('category')
                    for c in (parsed.get('categories') or [])
                    if c.get('category')
                }
            )

        return jsonify({'success': True, 'data': response_data}), 201
    except ValueError as e:
        return jsonify({'success': False, 'error': {'message': str(e)}}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/years/<int:year_id>/upload', methods=['POST'])
@require_jwt_auth
def upload_achievement(year_id):
    """Manual achievement entry for one distributor (lakhs)."""
    try:
        data = request.get_json()
        distributor = data.get('distributor_name')
        amount = data.get('amount')
        file_name = data.get('file_name', 'manual-entry')
        nick = (data.get('nick') or '').strip() or None

        if not distributor or amount is None:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400

        workspace_id = get_workspace_id()
        if not _get_year_or_404(year_id, workspace_id):
            return jsonify({'success': False, 'error': 'Year not found'}), 404

        db = _cdb()
        db.upsert_target_distributor_breakup(
            workspace_id=workspace_id,
            financial_year_id=year_id,
            distributor_name=distributor,
            achievement_lakhs=float(amount),
            nick=nick,
            source='manual',
        )
        total_lakhs = db.sync_financial_year_achievement_from_breakup(workspace_id, year_id)

        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO target_achievement_uploads (workspace_id, financial_year_id, distributor_name, calculated_total, file_name, uploaded_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (workspace_id, year_id, distributor, amount, file_name, datetime.now().isoformat()))
            conn.commit()
            upload_id = cursor.lastrowid
        except sqlite3.OperationalError:
            upload_id = None
        conn.close()

        return jsonify({'success': True, 'data': {'upload_id': upload_id, 'total_achievement_lakhs': total_lakhs}}), 201
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@target_achievement_bp.route('/summary', methods=['GET'])
@require_jwt_auth
def get_overall_summary():
    """Get overall summary across all years for the workspace"""
    try:
        workspace_id = get_workspace_id()
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT SUM(target_amount) as total_target,
                   SUM(COALESCE((SELECT SUM(calculated_total) FROM target_achievement_uploads WHERE financial_year_id = target_achievement_years.id AND workspace_id = ?), 0)) as total_achievement
            FROM target_achievement_years
            WHERE workspace_id = ?
        ''', (workspace_id, workspace_id))

        result = dict(cursor.fetchone() or {})
        conn.close()

        target = result.get('total_target', 0) or 0
        achievement = result.get('total_achievement', 0) or 0
        percentage = (achievement / target * 100) if target > 0 else 0

        return jsonify({'success': True, 'data': {'total_target': target, 'total_achievement': achievement, 'percentage': round(percentage, 2)}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

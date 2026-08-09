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

# DB stores targets in lakhs for backward compatibility.
# Clients enter full INR (e.g. 30000000 = 3 Crore) via target_rupees.
LAKH_RUPEES = 100_000.0
OTHERS_DISTRIBUTOR_NAME = "Others"


def _lakhs_to_rupees(lakhs) -> float:
    return round(float(lakhs or 0) * LAKH_RUPEES, 2)


def _rupees_to_lakhs(rupees) -> float:
    return round(float(rupees or 0) / LAKH_RUPEES, 6)


def _narrate_inr_cr_lakh(rupees) -> str:
    """30000000 → '3 Crore'; 1500000 → '15 Lakh'; 35000000 → '3 Crore 50 Lakh'."""
    n = int(round(float(rupees or 0)))
    if n <= 0:
        return "0"
    crore = n // 10_000_000
    rem = n % 10_000_000
    lakh = rem // 100_000
    rem_rs = rem % 100_000
    parts: list[str] = []
    if crore:
        parts.append(f"{crore} Crore")
    if lakh:
        parts.append(f"{lakh} Lakh")
    if rem_rs and not parts:
        parts.append(f"₹{n:,}")
    elif rem_rs and parts:
        # Keep narration at Cr/Lakh grain; ignore sub-lakh remainder.
        pass
    return " ".join(parts) if parts else "0"


def _money_payload(lakhs) -> dict:
    rupees = _lakhs_to_rupees(lakhs)
    return {
        "target_lakhs": float(lakhs or 0),
        "target_rupees": rupees,
        "target_narration": _narrate_inr_cr_lakh(rupees),
    }


def _resolve_target_lakhs_from_body(data: dict):
    """Prefer target_rupees (full INR); fallback target_lakhs / target."""
    if data.get("target_rupees") is not None:
        return _rupees_to_lakhs(data.get("target_rupees"))
    if data.get("target_lakhs") is not None:
        return float(data.get("target_lakhs"))
    if data.get("target") is not None:
        return float(data.get("target"))
    return None


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
    # Newest FY first (current year on top for mobile + dashboard).
    deduped.sort(key=lambda y: fiscal_year_sort_key(y.get("display_year")), reverse=True)
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
        cursor.execute(
            'SELECT * FROM target_achievement_years WHERE workspace_id = ?',
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
            'SELECT * FROM target_achievement_years WHERE workspace_id = ?',
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
                    "target_rupees": _lakhs_to_rupees(summary.get("target_lakhs") or 0),
                    "achievement_rupees": _lakhs_to_rupees(summary.get("active_achievement") or 0),
                    "target_narration": _narrate_inr_cr_lakh(
                        _lakhs_to_rupees(summary.get("target_lakhs") or 0)
                    ),
                    "achievement_narration": _narrate_inr_cr_lakh(
                        _lakhs_to_rupees(summary.get("active_achievement") or 0)
                    ),
                    "input_unit": "rupees",
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
        # Prefer full INR; fall back to lakhs for older clients.
        if data.get('target_rupees') is not None:
            target = _rupees_to_lakhs(data.get('target_rupees'))
            unit = 'lakhs'
        else:
            target = data.get('target')
            unit = (data.get('unit') or 'lakhs').lower()
            if target is None:
                target = 0

        if not year:
            return jsonify({'success': False, 'error': 'Year required'}), 400

        target = float(target or 0)
        workspace_id = get_workspace_id()
        _cdb().merge_duplicate_fiscal_years(workspace_id)
        conn = get_db()
        cursor = conn.cursor()
        existing = _find_year_by_normalized(conn, workspace_id, year)
        cols = {r[1] for r in cursor.execute('PRAGMA table_info(target_achievement_years)').fetchall()}
        now = datetime.now().isoformat()

        if existing:
            year_id = int(existing["id"])
            # Creating/opening an FY with target 0 must not wipe rolled-up distributor totals.
            if float(target or 0) == 0:
                conn.close()
                return jsonify(
                    {
                        'success': True,
                        'data': {
                            'year_id': year_id,
                            'year': year,
                            'target': existing.get('target') or existing.get('target_amount') or 0,
                            'unit': unit,
                            'updated_existing': True,
                        },
                    }
                ), 200
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
        # Keep FY target in sync with distributor sum (includes Others).
        rolled = _cdb().sync_financial_year_target_from_breakup(workspace_id, year_id)
        breakup = _cdb().list_target_distributor_breakup(workspace_id, year_id)
        rows = []
        has_others = False
        for r in breakup:
            name = (r.get("distributor_name") or "").strip()
            tl = float(r.get("target_lakhs") or 0)
            if tl <= 0 and name.lower() != OTHERS_DISTRIBUTOR_NAME.lower():
                continue
            if name.lower() == OTHERS_DISTRIBUTOR_NAME.lower():
                has_others = True
            money = _money_payload(tl)
            rows.append(
                {
                    "distributor_name": name,
                    "display_label": r.get("display_label") or name,
                    "is_others": name.lower() == OTHERS_DISTRIBUTOR_NAME.lower(),
                    "target_lakhs": money["target_lakhs"],
                    "target_rupees": money["target_rupees"],
                    "target_narration": money["target_narration"],
                }
            )
        if not has_others:
            money0 = _money_payload(0)
            rows.append(
                {
                    "distributor_name": OTHERS_DISTRIBUTOR_NAME,
                    "display_label": OTHERS_DISTRIBUTOR_NAME,
                    "is_others": True,
                    "target_lakhs": 0.0,
                    "target_rupees": 0.0,
                    "target_narration": money0["target_narration"],
                }
            )
        # Named distributors first, Others last.
        rows.sort(key=lambda x: (1 if x.get("is_others") else 0, (x.get("display_label") or "").lower()))
        fy_label = year.get("display_year") or year.get("financial_year") or year.get("year") or ""
        fy_money = _money_payload(rolled)
        return jsonify(
            {
                'success': True,
                'data': {
                    'fy_label': fy_label,
                    'year_id': year_id,
                    'target_lakhs': fy_money["target_lakhs"],
                    'target_rupees': fy_money["target_rupees"],
                    'target_narration': fy_money["target_narration"],
                    'rows': rows,
                    'unit': 'lakhs',
                    'input_unit': 'rupees',
                    'others_name': OTHERS_DISTRIBUTOR_NAME,
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
    """Set distributor-wise FY target. Prefer target_rupees (full INR); FY total = sum."""
    try:
        data = request.get_json(silent=True) or {}
        distributor_name = (data.get('distributor_name') or '').strip()
        target_lakhs = _resolve_target_lakhs_from_body(data)
        nick = (data.get('nick') or '').strip() or None
        if not distributor_name or target_lakhs is None:
            return jsonify({
                'success': False,
                'error': 'distributor_name and target_rupees (or target_lakhs) required',
            }), 400
        if float(target_lakhs) < 0:
            return jsonify({'success': False, 'error': 'target must be >= 0'}), 400

        workspace_id = get_workspace_id()
        if not _get_year_or_404(year_id, workspace_id):
            return jsonify({'success': False, 'error': 'Year not found'}), 404

        _cdb().set_target_distributor_target_lakhs(
            workspace_id=workspace_id,
            financial_year_id=year_id,
            distributor_name=distributor_name,
            target_lakhs=float(target_lakhs),
            nick=nick,
        )
        fy_total = _cdb().sync_financial_year_target_from_breakup(workspace_id, year_id)
        money = _money_payload(target_lakhs)
        fy_money = _money_payload(fy_total)
        return jsonify({
            'success': True,
            'data': {
                'distributor_name': distributor_name,
                'target_lakhs': money['target_lakhs'],
                'target_rupees': money['target_rupees'],
                'target_narration': money['target_narration'],
                'fy_target_lakhs': fy_money['target_lakhs'],
                'fy_target_rupees': fy_money['target_rupees'],
                'fy_target_narration': fy_money['target_narration'],
                'input_unit': 'rupees',
            },
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@target_achievement_bp.route('/years/<int:year_id>/distributor-target', methods=['DELETE'])
@require_jwt_auth
def delete_distributor_target(year_id):
    """Delete one distributor target (or reset Others to 0). Body: distributor_name."""
    try:
        data = request.get_json(silent=True) or {}
        distributor_name = (data.get('distributor_name') or request.args.get('distributor_name') or '').strip()
        if not distributor_name:
            return jsonify({'success': False, 'error': 'distributor_name required'}), 400
        workspace_id = get_workspace_id()
        year = _get_year_or_404(year_id, workspace_id)
        if not year:
            return jsonify({'success': False, 'error': 'Year not found'}), 404
        result = _cdb().delete_target_distributor_target(
            workspace_id=workspace_id,
            financial_year_id=year_id,
            distributor_name=distributor_name,
        )
        fy_money = _money_payload(result.get('fy_target_lakhs') or 0)
        return jsonify({
            'success': True,
            'data': {
                'distributor_name': result.get('distributor_name'),
                'is_others': result.get('is_others'),
                'fy_target_lakhs': fy_money['target_lakhs'],
                'fy_target_rupees': fy_money['target_rupees'],
                'fy_target_narration': fy_money['target_narration'],
            },
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@target_achievement_bp.route('/years/<int:year_id>', methods=['DELETE'])
@require_jwt_auth
def delete_year(year_id):
    """Permanently delete a fiscal year and its target/achievement breakup."""
    try:
        workspace_id = get_workspace_id()
        year = _get_year_or_404(year_id, workspace_id)
        if not year:
            return jsonify({'success': False, 'error': 'Year not found'}), 404
        fy_label = year.get('display_year') or year.get('financial_year') or year.get('year') or ''
        ok = _cdb().delete_financial_year_for_workspace(workspace_id, year_id)
        if not ok:
            return jsonify({'success': False, 'error': 'Unable to delete fiscal year'}), 500
        return jsonify({'success': True, 'data': {'year_id': year_id, 'fy_label': fy_label}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@target_achievement_bp.route('/monthly', methods=['GET'])
@require_jwt_auth
def get_monthly_distributor_data():
    """List distributor amounts for a calendar month (YYYY-MM)."""
    try:
        year_month = (request.args.get('year_month') or '').strip()
        if not year_month or len(year_month) < 7:
            return jsonify({'success': False, 'error': 'year_month required (YYYY-MM)'}), 400
        workspace_id = get_workspace_id()
        rows_raw = _cdb().list_monthly_distributor_entries(workspace_id, year_month)
        rows = []
        for r in rows_raw:
            money = _money_payload(r.get('amount_lakhs') or 0)
            rows.append({
                'distributor_name': r.get('distributor_name'),
                'nick': r.get('nick'),
                'amount_lakhs': money['target_lakhs'],
                'amount_rupees': money['target_rupees'],
                'amount_narration': money['target_narration'],
                'updated_at': r.get('updated_at'),
            })
        return jsonify({
            'success': True,
            'data': {
                'year_month': year_month,
                'rows': rows,
                'input_unit': 'rupees',
            },
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@target_achievement_bp.route('/monthly', methods=['POST'])
@require_jwt_auth
def save_monthly_distributor_data():
    """Save one distributor amount for a calendar month. Prefer amount_rupees."""
    try:
        data = request.get_json(silent=True) or {}
        year_month = (data.get('year_month') or '').strip()
        distributor_name = (data.get('distributor_name') or '').strip()
        nick = (data.get('nick') or '').strip() or None
        if data.get('amount_rupees') is not None:
            amount_lakhs = _rupees_to_lakhs(data.get('amount_rupees'))
        elif data.get('amount_lakhs') is not None:
            amount_lakhs = float(data.get('amount_lakhs'))
        else:
            amount_lakhs = None
        if not year_month or not distributor_name or amount_lakhs is None:
            return jsonify({
                'success': False,
                'error': 'year_month, distributor_name and amount_rupees required',
            }), 400
        if float(amount_lakhs) < 0:
            return jsonify({'success': False, 'error': 'amount must be >= 0'}), 400
        workspace_id = get_workspace_id()
        result = _cdb().upsert_monthly_distributor_entry(
            workspace_id=workspace_id,
            year_month=year_month,
            distributor_name=distributor_name,
            amount_lakhs=float(amount_lakhs),
            nick=nick,
        )
        money = _money_payload(result.get('amount_lakhs') or 0)
        return jsonify({
            'success': True,
            'data': {
                'year_month': year_month,
                'distributor_name': distributor_name,
                'amount_lakhs': money['target_lakhs'],
                'amount_rupees': money['target_rupees'],
                'amount_narration': money['target_narration'],
                'deleted': bool(result.get('deleted')),
            },
        }), 200
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

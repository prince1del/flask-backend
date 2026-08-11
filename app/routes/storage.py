from flask import Blueprint, request, jsonify, render_template_string
import json
import sqlite3

from app.routes.auth import require_jwt_auth, get_workspace_id
from app.storage.oauth import GoogleDriveOAuth
from centralized_db_system.db import CentralizedDB

# Create blueprint
storage_bp = Blueprint('storage', __name__, url_prefix='/api/v1/storage')

_DRIVE_CONNECTED_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Google Drive Connected</title>
  <style>
    body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
           font-family: system-ui, sans-serif; background:#0b1220; color:#eaf0fb; }
    .card { width:min(420px, 92vw); background:#121c30; border:1px solid rgba(37,224,255,.28);
            border-radius:18px; padding:28px 24px; text-align:center; }
    h1 { margin:0 0 8px; font-size:22px; letter-spacing:.08em; color:#7cf5ff; }
    p { margin:8px 0; color:#c5d0e8; line-height:1.45; }
    a.btn { display:inline-block; margin-top:16px; background:#25e0ff; color:#041018;
            font-weight:700; text-decoration:none; padding:12px 18px; border-radius:12px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>NEXORA</h1>
    <p><strong>Google Drive connected.</strong></p>
    <p>Tap below to return to the Nexora app.</p>
    <a class="btn" href="nexora://storage/connected">Return to Nexora</a>
  </div>
  <script>
    if (window.opener) {
      window.opener.postMessage({ type: "google_drive_connected" }, "*");
      setTimeout(function() { window.close(); }, 1200);
    } else {
      setTimeout(function() { window.location.href = "nexora://storage/connected"; }, 700);
    }
  </script>
</body>
</html>
"""


# ==================== ROUTES ====================

def _get_request_user():
    raw_user = getattr(request, 'user', None)
    if isinstance(raw_user, dict) and 'user_id' in raw_user and 'workspace_id' in raw_user:
        return raw_user
    raise RuntimeError("User context not available; authentication required.")

@storage_bp.route('/connect', methods=['POST'])
@require_jwt_auth
def connect_storage():
    """Initiate Google Drive OAuth connection"""
    try:
        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()

        host = request.host_url.rstrip('/')
        # Prefer env redirect URI, but never use localhost redirect when the
        # live request is already on a public host (mobile / Render).
        env_redirect = (GoogleDriveOAuth.REDIRECT_URI or "").strip()
        if env_redirect and "localhost" not in env_redirect and "127.0.0.1" not in env_redirect:
            redirect_uri = env_redirect
        else:
            redirect_uri = f"{host}/api/v1/storage/oauth-callback"

        oauth_url, state = GoogleDriveOAuth.get_auth_url(
            host_url=host,
            redirect_uri=redirect_uri,
            state_payload={
                'user_id': user_id,
                'workspace_id': workspace_id,
            },
        )
        return jsonify({
            'success': True,
            'data': {
                'oauth_url': oauth_url,
                'message': 'Redirect user to this URL to authorize',
                'state': state,
            }
        }), 200
    except ValueError as e:
        return jsonify({
            'success': False,
            'error': {
                'code': 'OAUTH_CONFIG',
                'message': str(e),
            },
        }), 400
    except RuntimeError as e:
        return jsonify({
            'success': False,
            'error': {
                'code': 'OAUTH_RUNTIME',
                'message': str(e),
            },
        }), 500
    except Exception as e:
        return jsonify({
            'success': False,
            'error': {
                'code': 'OAUTH_FAILED',
                'message': str(e),
            },
        }), 500

@storage_bp.route('/account', methods=['GET'])
@require_jwt_auth
def get_account():
    """Get connected storage account info"""
    try:
        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()

        db = CentralizedDB()
        account = db.get_storage_account(user_id=user_id, workspace_id=workspace_id)
        connected = bool(
            account
            and account.get("sync_status") == "connected"
            and account.get("oauth_token")
        )
        return jsonify(
            {
                "success": True,
                "data": {"connected": connected, "account": account if connected else None},
            }
        ), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@storage_bp.route('/oauth-callback', methods=['GET'])
def oauth_callback():
    """Handle OAuth callback from Google."""
    try:
        code = request.args.get('code')
        state = request.args.get('state')
        if not code:
            return render_template_string(
                '<h1>Google Drive Connect Failed</h1><p>No authorization code was returned.</p>'
            ), 400

        state_data = GoogleDriveOAuth.parse_oauth_state(state)
        user_id = state_data.get('user_id')
        workspace_id = (
            state_data.get('workspace_id')
            or state_data.get('work_id')
            or 'default'
        )
        if user_id is None:
            return render_template_string(
                '<h1>Google Drive Connect Failed</h1><p>Missing user context in OAuth state. Please reconnect from Cloud Hub.</p>'
            ), 400

        host = request.host_url.rstrip('/')
        env_redirect = (GoogleDriveOAuth.REDIRECT_URI or "").strip()
        if env_redirect and "localhost" not in env_redirect and "127.0.0.1" not in env_redirect:
            redirect_uri = env_redirect
        else:
            redirect_uri = f'{host}/api/v1/storage/oauth-callback'
        try:
            token_data = GoogleDriveOAuth.exchange_code_for_token(
                auth_code=code,
                host_url=host,
                redirect_uri=redirect_uri,
            )
        except ValueError as exc:
            return render_template_string(
                '<h1>Google Drive Connect Failed</h1><p>{{ message }}</p>',
                message=str(exc),
            ), 400
        except RuntimeError as exc:
            return render_template_string(
                '<h1>Google Drive Connect Failed</h1><p>{{ message }}</p>',
                message=str(exc),
            ), 500

        db = CentralizedDB()
        db.ensure_storage_tables()
        db.save_storage_account(
            user_id=int(user_id),
            workspace_id=str(workspace_id),
            provider_type='google_drive',
            oauth_token=token_data,
            sync_status='connected',
        )

        return render_template_string(_DRIVE_CONNECTED_HTML), 200
    except Exception as e:
        return render_template_string(
            '<!doctype html>'
            '<html><head><meta charset="utf-8"><title>Google Drive Connect Failed</title></head>'
            '<body>'
            '<h1>Google Drive Connect Failed</h1>'
            '<p>{{ message }}</p>'
            '<script>'
            'const errorMessage = {{ message|tojson }};'
            'if (window.opener) {'
            '  window.opener.postMessage({ type: "google_drive_connection_failed", message: errorMessage }, "*");'
            '}'
            '</script>'
            '</body></html>',
            message=str(e),
        ), 500

@storage_bp.route('/disconnect', methods=['POST'])
@require_jwt_auth
def disconnect_storage():
    """Disconnect Google Drive storage"""
    try:
        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()

        db = CentralizedDB()
        disconnected = db.disconnect_storage_account(
            user_id=user_id,
            workspace_id=workspace_id,
            provider_type='google_drive',
        )
        if not disconnected:
            return jsonify({'success': False, 'error': 'No connected Google Drive account found.'}), 404
        return jsonify({'success': True, 'data': {'message': 'Disconnected successfully'}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@storage_bp.route('/dashboard', methods=['GET'])
@require_jwt_auth
def get_dashboard():
    """Get storage dashboard with statistics"""
    try:
        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()

        db = CentralizedDB()
        account = db.get_storage_account(user_id=user_id, workspace_id=workspace_id)

        if not account:
            return jsonify({'success': True, 'data': {'connected': False, 'storage_info': {}, 'user_id': user_id}}), 200

        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) as file_count,
                   SUM(COALESCE(fi.file_size_bytes, fi.file_size, 0)) as total_size
            FROM file_index fi
            JOIN storage_accounts sa ON fi.storage_account_id = sa.id
            WHERE sa.user_id = ? AND sa.workspace_id = ?
        ''', (user_id, workspace_id))

        stats = dict(cursor.fetchone() or {})
        conn.close()

        return jsonify({'success': True, 'data': {'connected': True, 'storage_info': {'file_count': stats.get('file_count', 0), 'total_size': stats.get('total_size', 0), 'quota': 107374182400}, 'user_id': user_id}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@storage_bp.route('/files', methods=['GET'])
@require_jwt_auth
def get_files():
    """Get list of indexed files"""
    try:
        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()

        db = CentralizedDB()
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT fi.*
            FROM file_index fi
            JOIN storage_accounts sa ON fi.storage_account_id = sa.id
            WHERE sa.user_id = ? AND sa.workspace_id = ?
            ORDER BY COALESCE(fi.modified_at, fi.updated_at, fi.created_at) DESC
            LIMIT 500
        ''', (user_id, workspace_id))

        files = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify({'success': True, 'data': {'files': files}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@storage_bp.route('/files/<file_id>/download', methods=['GET'])
@require_jwt_auth
def download_file(file_id):
    """Download a Drive file via the connected NEXORA OAuth token (not browser Google session)."""
    from flask import Response
    from urllib.parse import quote
    from app.storage.manager import StorageManager
    from app.storage.providers.google_drive_provider import GoogleDriveProvider

    try:
        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()
        safe_id = str(file_id or '').strip()
        if not safe_id:
            return jsonify({'success': False, 'error': 'file_id required'}), 400

        # Ensure the file belongs to this user's indexed Drive (or allow direct Drive id if connected)
        db = CentralizedDB()
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            '''
            SELECT fi.file_id, fi.file_name, fi.mime_type, fi.file_type
            FROM file_index fi
            JOIN storage_accounts sa ON fi.storage_account_id = sa.id
            WHERE sa.user_id = ? AND sa.workspace_id = ? AND fi.file_id = ?
            LIMIT 1
            ''',
            (user_id, workspace_id, safe_id),
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({
                'success': False,
                'error': 'File not found in your Cloud Hub index. Click Sync, then try again.',
            }), 404

        mime_hint = str(row['mime_type'] or row['file_type'] or '')
        if 'folder' in mime_hint.lower():
            return jsonify({'success': False, 'error': 'Folders cannot be downloaded.'}), 400

        manager = StorageManager()
        manager.register_provider('google_drive', GoogleDriveProvider)
        payload = manager.download_file_bytes(
            user_id=user_id,
            file_id=safe_id,
            workspace_id=workspace_id,
        )
        content = payload.get('content') or b''
        filename = payload.get('file_name') or row['file_name'] or safe_id
        mime_type = payload.get('mime_type') or 'application/octet-stream'
        # RFC 5987 filename for non-ASCII names
        disposition = (
            f"attachment; filename=\"{filename.replace('\"', '')}\"; "
            f"filename*=UTF-8''{quote(filename)}"
        )
        return Response(
            content,
            mimetype=mime_type,
            headers={
                'Content-Disposition': disposition,
                'Content-Length': str(len(content)),
                'Cache-Control': 'no-store',
            },
        )
    except KeyError as e:
        return jsonify({'success': False, 'error': str(e) or 'Google Drive is not connected.'}), 400
    except Exception as e:
        message = str(e)
        if 'File not found' in message or '404' in message:
            message = 'File not found on Google Drive (it may have been deleted or moved).'
        return jsonify({'success': False, 'error': message}), 500


@storage_bp.route('/sync', methods=['POST'])
@require_jwt_auth
def sync_files():
    """Sync files from Google Drive into the local file index."""
    try:
        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()

        from app.storage.manager import StorageManager
        from app.storage.providers.google_drive_provider import GoogleDriveProvider

        manager = StorageManager()
        manager.register_provider('google_drive', GoogleDriveProvider)
        result = manager.sync_user_storage(
            user_id=user_id,
            workspace_id=workspace_id,
            incremental=False,
        )
        synced = int(result.get('synced_items') or 0)
        found = int(result.get('files_found') or synced)
        return jsonify({
            'success': True,
            'data': {
                'message': f'Synced {synced} file(s) from Google Drive.',
                'files_synced': synced,
                'files_found': found,
                'workspace_id': workspace_id,
            },
        }), 200
    except KeyError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        message = str(e)
        if 'accessNotConfigured' in message or 'Drive API has not been used' in message:
            message = (
                'Google Drive API is not enabled for this OAuth project. '
                'Enable it at https://console.developers.google.com/apis/api/drive.googleapis.com/overview?project=318831882178 '
                'then wait a minute and click Sync again.'
            )
        return jsonify({'success': False, 'error': message}), 500

@storage_bp.route('/search', methods=['GET'])
@require_jwt_auth
def search_files():
    """Search indexed files"""
    try:
        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()
        query = request.args.get('query', '').lower()

        if not query:
            return jsonify({'success': False, 'error': 'Query required'}), 400

        db = CentralizedDB()
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT fi.*
            FROM file_index fi
            JOIN storage_accounts sa ON fi.storage_account_id = sa.id
            WHERE sa.user_id = ? AND sa.workspace_id = ?
              AND (
                LOWER(fi.file_name) LIKE ? OR
                LOWER(fi.file_type) LIKE ? OR
                LOWER(fi.tags) LIKE ?
              )
            ORDER BY fi.created_at DESC
            LIMIT 50
        ''', (user_id, workspace_id, f'%{query}%', f'%{query}%', f'%{query}%'))

        files = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify({'success': True, 'data': {'results': files}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

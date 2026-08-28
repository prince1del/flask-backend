import io

from flask import Blueprint, current_app, request, jsonify, render_template_string

from app.routes.auth import require_jwt_auth, get_workspace_id
from app.storage.gmail_oauth import GmailOAuth
from centralized_db_system.db import CentralizedDB

mail_sync_bp = Blueprint('mail_sync', __name__, url_prefix='/api/v1/mail-sync')

_GMAIL_CONNECTED_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gmail Connected</title>
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
    <p><strong>Gmail connected.</strong></p>
    <p>Sales Order / Commercial Invoice PDFs in this inbox can now be auto-imported.</p>
    <a class="btn" href="nexora://mail-sync/connected">Return to Nexora</a>
  </div>
  <script>
    if (window.opener) {
      window.opener.postMessage({ type: "gmail_sync_connected" }, "*");
      setTimeout(function() { window.close(); }, 1200);
    } else {
      setTimeout(function() { window.location.href = "nexora://mail-sync/connected"; }, 700);
    }
  </script>
</body>
</html>
"""


def _db() -> CentralizedDB:
    """The same database every other route uses.

    A bare `CentralizedDB()` resolves only CLOUD_DATABASE_URL/DATABASE_URL and
    otherwise falls back to a relative `centralized_db.sqlite3` â€” it ignores
    `DATABASE_PATH` and `current_app.config["DATABASE_PATH"]`, which is what
    `app/routes/data.py` and `app/routes/auth.py` actually run on. Mail sync
    used to construct it bare, so the Gmail token, the processed-message log and
    the pending imports lived in a file that held no distributors, no company
    profile and no Order Desk â€” every poll therefore matched nothing and
    imported nothing into the user's real workspace.
    """
    from app.routes.data import _db_path

    return CentralizedDB(_db_path())


def _get_request_user():
    raw_user = getattr(request, 'user', None)
    if isinstance(raw_user, dict) and 'user_id' in raw_user and 'workspace_id' in raw_user:
        return raw_user
    raise RuntimeError("User context not available; authentication required.")


@mail_sync_bp.route('/connect', methods=['POST'])
@require_jwt_auth
def connect_gmail():
    """Initiate Gmail (read-only) OAuth connection for CI/SO auto-import."""
    try:
        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()

        host = request.host_url.rstrip('/')
        env_redirect = GmailOAuth._env("GOOGLE_GMAIL_OAUTH_REDIRECT_URI")
        if env_redirect and "localhost" not in env_redirect and "127.0.0.1" not in env_redirect:
            redirect_uri = env_redirect
        else:
            redirect_uri = f"{host}/api/v1/mail-sync/oauth-callback"

        oauth_url, state = GmailOAuth.get_auth_url(
            host_url=host,
            redirect_uri=redirect_uri,
            state_payload={'user_id': user_id, 'workspace_id': workspace_id},
        )
        return jsonify({
            'success': True,
            'data': {
                'oauth_url': oauth_url,
                'message': 'Redirect user to this URL to authorize Gmail read-only access',
                'state': state,
            }
        }), 200
    except ValueError as e:
        return jsonify({'success': False, 'error': {'code': 'OAUTH_CONFIG', 'message': str(e)}}), 400
    except RuntimeError as e:
        return jsonify({'success': False, 'error': {'code': 'OAUTH_RUNTIME', 'message': str(e)}}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': {'code': 'OAUTH_FAILED', 'message': str(e)}}), 500


@mail_sync_bp.route('/oauth-callback', methods=['GET'])
def oauth_callback():
    """Handle OAuth callback from Google for the Gmail read-only grant."""
    try:
        code = request.args.get('code')
        state = request.args.get('state')
        if not code:
            return render_template_string(
                '<h1>Gmail Connect Failed</h1><p>No authorization code was returned.</p>'
            ), 400

        state_data = GmailOAuth.parse_oauth_state(state)
        user_id = state_data.get('user_id')
        workspace_id = state_data.get('workspace_id') or 'default'
        if user_id is None:
            return render_template_string(
                '<h1>Gmail Connect Failed</h1><p>Missing user context in OAuth state. '
                'Please reconnect from Settings.</p>'
            ), 400

        host = request.host_url.rstrip('/')
        env_redirect = GmailOAuth._env("GOOGLE_GMAIL_OAUTH_REDIRECT_URI")
        if env_redirect and "localhost" not in env_redirect and "127.0.0.1" not in env_redirect:
            redirect_uri = env_redirect
        else:
            redirect_uri = f'{host}/api/v1/mail-sync/oauth-callback'
        try:
            token_data = GmailOAuth.exchange_code_for_token(
                auth_code=code, host_url=host, redirect_uri=redirect_uri,
            )
        except ValueError as exc:
            return render_template_string('<h1>Gmail Connect Failed</h1><p>{{ message }}</p>', message=str(exc)), 400
        except RuntimeError as exc:
            return render_template_string('<h1>Gmail Connect Failed</h1><p>{{ message }}</p>', message=str(exc)), 500

        db = _db()
        db.save_storage_account(
            user_id=int(user_id),
            workspace_id=str(workspace_id),
            provider_type='gmail',
            oauth_token=token_data,
            sync_status='connected',
        )
        return render_template_string(_GMAIL_CONNECTED_HTML), 200
    except Exception as e:
        return render_template_string(
            '<!doctype html><html><head><meta charset="utf-8"><title>Gmail Connect Failed</title></head>'
            '<body><h1>Gmail Connect Failed</h1><p>{{ message }}</p></body></html>',
            message=str(e),
        ), 500


@mail_sync_bp.route('/status', methods=['GET'])
@require_jwt_auth
def status():
    try:
        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()

        db = _db()
        account = db.get_storage_account(user_id=user_id, provider_type='gmail', workspace_id=workspace_id)
        connected = bool(account and account.get('sync_status') == 'connected' and account.get('oauth_token'))
        return jsonify({
            'success': True,
            'data': {
                'connected': connected,
                'last_sync': account.get('last_sync') if account else None,
            },
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@mail_sync_bp.route('/disconnect', methods=['POST'])
@require_jwt_auth
def disconnect():
    try:
        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()

        db = _db()
        disconnected = db.disconnect_storage_account(user_id=user_id, workspace_id=workspace_id, provider_type='gmail')
        if not disconnected:
            return jsonify({'success': False, 'error': 'No connected Gmail account found.'}), 404
        return jsonify({'success': True, 'data': {'message': 'Disconnected successfully'}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@mail_sync_bp.route('/poll', methods=['POST'])
@require_jwt_auth
def poll():
    """Manually trigger a Gmail scan for new CI/SO PDF attachments.

    No scheduler runs this automatically yet â€” call it from the app (e.g. a
    'Check Mail' button, or on app-open) until a decision is made on cron vs
    app-triggered polling.
    """
    try:
        import sqlite3

        from app.services.gmail_ci_so_sync import poll_for_user

        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()
        max_messages = request.args.get('max_messages', default=15, type=int) or 15
        reset_history = request.args.get('reset', default='false').lower() in ('1', 'true', 'yes')

        if reset_history:
            # Clear old pending imports when user asks to rescan from scratch so stale unconfirmed items don't accumulate
            db_inst = _db()
            db_inst.ensure_gmail_pending_imports_table()
            with sqlite3.connect(str(db_inst.db_path)) as conn:
                conn.execute(
                    "DELETE FROM gmail_pending_imports WHERE user_id = ? AND workspace_id = ?",
                    (user_id, str(workspace_id or "default")),
                )
                conn.commit()

        summary = poll_for_user(
            user_id=user_id,
            workspace_id=workspace_id,
            max_messages=max_messages,
            reset_history=reset_history,
        )
        return jsonify({'success': True, 'data': summary}), 200
    except RuntimeError as e:
        return jsonify({'success': False, 'error': {'code': 'NOT_CONNECTED', 'message': str(e)}}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': {'code': 'POLL_FAILED', 'message': str(e)}}), 500


@mail_sync_bp.route('/diagnostics', methods=['GET'])
@require_jwt_auth
def poll_diagnostics():
    """Why recent polls produced nothing â€” own rows; the owner sees the workspace.

    Read-only support API (no new screen), mirroring
    /api/v1/order-fulfillment/so-pack/upload-diagnostics: the owner flag comes
    from the signed JWT, never from request input.
    """
    import sqlite3

    from app.services import mail_sync_diagnostics as diag

    try:
        user = _get_request_user()
        user_id = int(user['user_id'])
        try:
            from app.routes.auth import is_request_workspace_owner

            is_owner = bool(is_request_workspace_owner())
        except Exception:
            is_owner = False

        db = _db()
        conn = sqlite3.connect(str(db.db_path))
        try:
            rows = diag.list_recent(
                conn,
                user_id=user_id,
                workspace_wide=is_owner,
                limit=request.args.get('limit', default=50, type=int) or 50,
            )
        finally:
            conn.close()
        return jsonify({
            'success': True,
            'data': {'records': rows, 'scope': 'workspace' if is_owner else 'mine'},
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@mail_sync_bp.route('/log', methods=['GET'])
@require_jwt_auth
def get_import_log():
    """Permanent history of every CI/SO the poller ever classified from
    Gmail/WeTransfer â€” source, outcome, and (if it became a real Order
    Desk record) the tracking_id to cross-reference there."""
    try:
        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()

        db = _db()
        limit = request.args.get('limit', default=200, type=int) or 200
        rows = db.list_gmail_import_log(user_id=user_id, workspace_id=workspace_id, limit=limit)
        return jsonify({'success': True, 'data': {'items': rows}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@mail_sync_bp.route('/pending', methods=['GET'])
@require_jwt_auth
def list_pending():
    """CI/SO the poller found but couldn't safely auto-confirm (ambiguous
    match) — needs a human to pick a distributor or dismiss it."""
    try:
        import json as _json

        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()

        db = _db()
        rows = db.list_gmail_pending_imports(user_id=user_id, workspace_id=workspace_id, status='pending')
        # Parse preview_json and deduplicate rows by doc_no / filename in case historical duplicate rows exist
        enriched = []
        seen_keys = set()
        for r in rows:
            item = dict(r)
            dedup_key = (item.get('kind'), item.get('doc_no') or item.get('filename'))
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            if item.get('preview_json'):
                try:
                    item['preview'] = _json.loads(item['preview_json'])
                except Exception:
                    item['preview'] = None
            enriched.append(item)
        return jsonify({'success': True, 'data': {'items': enriched}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@mail_sync_bp.route('/pending/<int:pending_id>/confirm', methods=['POST'])
@require_jwt_auth
def confirm_pending(pending_id: int):
    try:
        import json as _json

        from app.routes.data import _confirm_ci_so_link_impl, _confirm_ci_only_impl

        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()
        body = request.get_json(silent=True) or {}
        distributor_id = body.get('distributor_id')

        db = _db()
        row = db.get_gmail_pending_import(pending_id, user_id=user_id, workspace_id=workspace_id)
        if not row or row.get('status') != 'pending':
            return jsonify({'success': False, 'error': {'message': 'Pending import not found'}}), 404

        if row['kind'] == 'CI':
            preview = _json.loads(row.get('preview_json') or '{}')
            if preview.get('no_match_found'):
                if not distributor_id:
                    return jsonify({
                        'success': False,
                        'error': {'code': 'DISTRIBUTOR_REQUIRED', 'message': 'distributor_id is required'},
                    }), 400
                resp = _confirm_ci_only_impl({
                    'order_ref_no': preview.get('order_ref_no'),
                    'invoice_no': preview.get('invoice_no'),
                    'distributor_id': distributor_id,
                    'commercial_invoice_file_reference': preview.get('commercial_invoice_file_reference'),
                    'commercial_invoice_parsed': preview.get('commercial_invoice_parsed'),
                    'amount': preview.get('extracted_amount'),
                    'acknowledge_party_mismatch': True,
                })
            else:
                resp = _confirm_ci_so_link_impl({
                    'order_ref_no': preview.get('order_ref_no'),
                    'commercial_invoice_file_reference': preview.get('commercial_invoice_file_reference'),
                    'commercial_invoice_parsed': preview.get('commercial_invoice_parsed'),
                    'amount': preview.get('extracted_amount'),
                })
            result = resp.get_json(silent=True) or {}
            if not result.get('success') or (result.get('data') or {}).get('link_error'):
                return jsonify({
                    'success': False,
                    'error': {'message': (result.get('data') or {}).get('link_error')
                              or (result.get('error') or {}).get('message') or 'Confirm failed'},
                }), 400
        else:
            if not distributor_id:
                return jsonify({
                    'success': False,
                    'error': {'code': 'DISTRIBUTOR_REQUIRED', 'message': 'distributor_id is required'},
                }), 400
            file_bytes = row.get('file_bytes')
            if not file_bytes:
                return jsonify({'success': False, 'error': {'message': 'Original file no longer available'}}), 400
            client = current_app.test_client()
            confirm_resp = client.post(
                '/api/v1/order-fulfillment/upload/sales-order',
                data={
                    'file': (io.BytesIO(file_bytes), row.get('filename') or 'so.pdf'),
                    'distributor_id': str(distributor_id),
                },
                headers={'Authorization': request.headers.get('Authorization')},
                content_type='multipart/form-data',
            )
            result = confirm_resp.get_json(silent=True) or {}
            data = result.get('data') or {}
            if not result.get('success') or not data.get('tracking_id') or data.get('link_error'):
                return jsonify({
                    'success': False,
                    'error': {'message': data.get('link_error')
                              or (result.get('error') or {}).get('message') or 'Confirm failed'},
                }), 400

        db.update_gmail_pending_import_status(pending_id, 'confirmed')
        return jsonify({'success': True, 'data': {'message': 'Confirmed'}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@mail_sync_bp.route('/pending/<int:pending_id>/reject', methods=['POST'])
@require_jwt_auth
def reject_pending(pending_id: int):
    try:
        user = _get_request_user()
        user_id = user['user_id']
        workspace_id = get_workspace_id()

        db = _db()
        row = db.get_gmail_pending_import(pending_id, user_id=user_id, workspace_id=workspace_id)
        if not row:
            return jsonify({'success': False, 'error': {'message': 'Pending import not found'}}), 404
        db.update_gmail_pending_import_status(pending_id, 'rejected')
        return jsonify({'success': True, 'data': {'message': 'Dismissed'}}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

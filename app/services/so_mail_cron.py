"""Background SO-mail scan for every user with Gmail connected.

Run by the nexora-so-mail systemd timer (every 30 minutes):

    python -m app.services.so_mail_cron

Each user's scan runs inside a request context carrying that user, so the
auto-save path calls Order Desk's match exactly as a logged-in request would.
"""

from __future__ import annotations

import logging
import sqlite3
import sys

logger = logging.getLogger("so_mail_cron")


def _connected_users(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT s.user_id, s.workspace_id, u.username, u.role,
                      COALESCE(u.is_workspace_owner, 0)
               FROM storage_accounts s JOIN users u ON u.id = s.user_id
               WHERE s.provider_type = 'gmail' AND s.sync_status = 'connected'
                 AND s.oauth_token IS NOT NULL AND COALESCE(u.status, 'active') = 'active'"""
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [{"user_id": r[0], "workspace_id": r[1], "username": r[2], "role": r[3],
             "is_workspace_owner": bool(r[4])} for r in rows]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from flask import request

    from app.routes.data import _db_path
    from app.services import so_mail_import as smi
    from app.web_app import create_app

    app = create_app()
    failures = 0
    with app.app_context():
        users = _connected_users(_db_path())
        for u in users:
            with app.test_request_context("/internal/so-mail-cron"):
                request.user = {"user_id": u["user_id"], "id": u["user_id"],
                                "workspace_id": u["workspace_id"], "username": u["username"],
                                "role": u["role"], "is_workspace_owner": u["is_workspace_owner"]}
                try:
                    out = smi.scan_mailbox(u["user_id"], u["workspace_id"], max_messages=50)
                    logger.info("user %s: matched=%s staged=%s auto_saved=%s errors=%s",
                                u["user_id"], out.get("matched"), out.get("staged"),
                                out.get("auto_saved", 0), len(out.get("errors") or []))
                except Exception:
                    failures += 1
                    logger.exception("SO mail scan failed for user %s", u["user_id"])
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

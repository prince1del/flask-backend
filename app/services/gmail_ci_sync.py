"""Gmail transport for Commercial Invoice (CI) import.

Fetches PDF attachments from the inbox and hands each one to
`_ingest_one_ci_pdf()` — the same unified entry point used by manual
upload and bulk upload. No parallel save path.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

MAIL_WINDOW_DAYS = 60

GMAIL_QUERY = (
    f"newer_than:{MAIL_WINDOW_DAYS}d has:attachment filename:pdf "
    '(subject:(invoice OR "commercial invoice" OR "tax invoice") '
    'OR filename:(invoice OR ci OR "commercial invoice"))'
)

GMAIL_READONLY_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def build_gmail_service(oauth_token: dict[str, Any]):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_info(oauth_token, GMAIL_READONLY_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


def _extract_message_pdfs(
    service, message_id: str
) -> tuple[str, str, list[tuple[str, bytes]]]:
    """Return (subject, email_date_iso, [(filename, pdf_bytes), ...])."""
    import datetime

    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    subject = ""
    for header in (msg.get("payload", {}) or {}).get("headers") or []:
        if str(header.get("name", "")).lower() == "subject":
            subject = header.get("value", "") or ""
            break

    email_date = ""
    internal_date_ms = msg.get("internalDate")
    if internal_date_ms:
        try:
            email_date = datetime.datetime.fromtimestamp(
                int(internal_date_ms) / 1000, tz=datetime.timezone.utc
            ).isoformat()
        except (TypeError, ValueError):
            email_date = ""

    attachments: list[tuple[str, bytes]] = []

    def walk(part: dict[str, Any]) -> None:
        filename = part.get("filename") or ""
        body = part.get("body") or {}
        if filename.lower().endswith(".pdf") and body.get("attachmentId"):
            att = (
                service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=body["attachmentId"])
                .execute()
            )
            data = base64.urlsafe_b64decode(att["data"])
            attachments.append((filename, data))
        for sub_part in part.get("parts") or []:
            walk(sub_part)

    walk(msg.get("payload") or {})
    return subject, email_date, attachments


def _pdf_has_text_layer(data: bytes) -> bool:
    from app.three_step_verification import _extract_pdf_text

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        with os.fdopen(fd, "wb") as tmp_f:
            tmp_f.write(data)
        return bool((_extract_pdf_text(tmp_path) or "").strip())
    except Exception:
        return False
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _finish(db, *, user_id: int, workspace_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    import sqlite3

    from app.services import mail_sync_diagnostics as diag

    assessment = diag.assess(summary)
    summary["outcome"] = assessment["outcome"]
    summary["message"] = diag.message_for(assessment)
    try:
        conn = sqlite3.connect(str(db.db_path))
        try:
            diag.record(
                conn,
                user_id=user_id,
                workspace_id=workspace_id,
                query=GMAIL_QUERY,
                summary=summary,
                assessment=assessment,
            )
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to record mail sync diagnostics for user %s", user_id)
    return summary


def poll_for_user(
    user_id: int, workspace_id: str, max_messages: int = 15, reset_history: bool = False
) -> dict[str, Any]:
    """Scan Gmail for CI PDFs and import via `_ingest_one_ci_pdf()`."""
    from app.routes.data import _db_path, _ingest_one_ci_pdf
    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(_db_path())
    empty = {
        "connected": False,
        "scanned": 0,
        "ci_imported": 0,
        "so_staged": 0,
        "skipped": 0,
        "errors": [],
        "imported_items": [],
        "pending_review": 0,
        "duplicates": 0,
        "debug": [],
        "messages_matched": 0,
        "attachments_seen": 0,
        "attachments_unreadable": 0,
        "unreadable_files": [],
        "skipped_reasons": [],
        "window_days": MAIL_WINDOW_DAYS,
    }

    account = db.get_storage_account(
        user_id=user_id, provider_type="gmail", workspace_id=workspace_id
    )
    if not account or account.get("sync_status") != "connected" or not account.get("oauth_token"):
        return _finish(db, user_id=user_id, workspace_id=workspace_id, summary=empty)

    if reset_history:
        db.clear_processed_gmail_messages(user_id=user_id, workspace_id=workspace_id)

    service = build_gmail_service(account["oauth_token"])
    processed_ids = db.get_processed_gmail_message_ids(user_id=user_id, workspace_id=workspace_id)

    listing = (
        service.users()
        .messages()
        .list(userId="me", q=GMAIL_QUERY, maxResults=max_messages)
        .execute()
    )
    messages = listing.get("messages") or []

    summary: dict[str, Any] = {
        "connected": True,
        "scanned": 0,
        "ci_imported": 0,
        "so_staged": 0,
        "skipped": 0,
        "errors": [],
        "imported_items": [],
        "pending_review": 0,
        "duplicates": 0,
        "debug": [],
        "messages_matched": len(messages),
        "attachments_seen": 0,
        "attachments_unreadable": 0,
        "unreadable_files": [],
        "skipped_reasons": [],
        "window_days": MAIL_WINDOW_DAYS,
    }

    for m in messages:
        message_id = m["id"]
        if message_id in processed_ids:
            continue
        summary["scanned"] += 1
        try:
            subject, email_date, attachments = _extract_message_pdfs(service, message_id)
            if not attachments:
                summary["skipped"] += 1
                db.mark_gmail_message_processed(
                    user_id=user_id, workspace_id=workspace_id, message_id=message_id
                )
                continue

            handled_any = False
            for filename, data in attachments:
                summary["attachments_seen"] += 1
                if not _pdf_has_text_layer(data):
                    summary["attachments_unreadable"] += 1
                    summary["unreadable_files"].append(
                        {
                            "message_id": message_id,
                            "subject": subject,
                            "filename": filename,
                            "reason": "no_text_layer",
                        }
                    )
                    continue

                handled_any = True
                try:
                    result = _ingest_one_ci_pdf(filename, data)
                except Exception as exc:
                    logger.exception("CI ingest failed for %s (message %s)", filename, message_id)
                    summary["errors"].append(
                        {"message_id": message_id, "filename": filename, "error": str(exc)}
                    )
                    continue

                state = result.get("state")
                doc_no = result.get("invoice_no") or result.get("order_ref_no")
                preview = result.get("preview") if isinstance(result.get("preview"), dict) else {}
                party_name = preview.get("buyer_name")

                if state == "ok":
                    summary["ci_imported"] += 1
                    summary["imported_items"].append(
                        {
                            "kind": "CI",
                            "filename": filename,
                            "email_date": email_date,
                            "doc_no": doc_no,
                            "party_name": party_name,
                            "auto_confirmed": True,
                            "confirm_status": result.get("status"),
                        }
                    )
                    db.log_gmail_import(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        message_id=message_id,
                        source="gmail",
                        kind="CI",
                        filename=filename,
                        doc_no=doc_no,
                        party_name=party_name,
                        outcome="auto_confirmed",
                        tracking_id=result.get("tracking_id"),
                        detail=result.get("status"),
                        email_date=email_date,
                    )
                elif state == "dup":
                    summary["duplicates"] += 1
                    db.log_gmail_import(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        message_id=message_id,
                        source="gmail",
                        kind="CI",
                        filename=filename,
                        doc_no=doc_no,
                        party_name=party_name,
                        outcome="duplicate",
                        detail=result.get("status"),
                        email_date=email_date,
                    )
                elif state == "review":
                    db.save_gmail_pending_import(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        message_id=message_id,
                        kind="CI",
                        filename=filename,
                        doc_no=doc_no,
                        party_name=party_name,
                        reason=result.get("status"),
                        preview_json=json.dumps(preview, default=str),
                    )
                    summary["pending_review"] += 1
                    db.log_gmail_import(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        message_id=message_id,
                        source="gmail",
                        kind="CI",
                        filename=filename,
                        doc_no=doc_no,
                        party_name=party_name,
                        outcome="pending_review",
                        detail=result.get("status"),
                        email_date=email_date,
                    )
                else:
                    summary["skipped_reasons"].append(
                        {
                            "message_id": message_id,
                            "subject": subject,
                            "filename": filename,
                            "reason": result.get("status") or "not_a_commercial_invoice",
                        }
                    )
                    db.log_gmail_import(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        message_id=message_id,
                        source="gmail",
                        kind="CI",
                        filename=filename,
                        doc_no=doc_no,
                        party_name=party_name,
                        outcome="error",
                        detail=result.get("status"),
                        email_date=email_date,
                    )

            if not handled_any and attachments:
                summary["skipped"] += 1
            db.mark_gmail_message_processed(
                user_id=user_id, workspace_id=workspace_id, message_id=message_id
            )
        except Exception as exc:
            logger.exception("Gmail CI poll failed for message %s", message_id)
            summary["errors"].append({"message_id": message_id, "error": str(exc)})

    return _finish(db, user_id=user_id, workspace_id=workspace_id, summary=summary)

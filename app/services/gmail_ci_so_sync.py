"""Auto-import Commercial Invoice (CI) / Sales Order (SO) PDFs found as Gmail
attachments, by feeding them through the exact same upload endpoints a
founder would use for a manual upload — this only automates finding and
attaching the file, it does not change how CI/SO are parsed, matched, or
persisted.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)

CI_KEYWORDS = (
    "commercial invoice",
    "tax invoice",
    "gst invoice",
    "invoice no",
    "invoice number",
)
SO_KEYWORDS = (
    "sales order",
    "purchase order",
    "order ref",
    "po number",
    "buyer code",
)

GMAIL_QUERY = (
    'has:attachment filename:pdf newer_than:60d '
    '(subject:(invoice OR "sales order" OR "purchase order" OR "commercial invoice") '
    'OR filename:(invoice OR order))'
)

GMAIL_READONLY_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def build_gmail_service(oauth_token: dict[str, Any]):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_info(oauth_token, GMAIL_READONLY_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


def _classify_pdf(subject: str, filename: str, text_sample: str) -> str | None:
    """Best-effort CI vs SO guess from subject/filename/PDF text. Returns
    None when neither looks like a match (email gets skipped, not imported)."""
    haystack = f"{subject}\n{filename}\n{text_sample}".lower()
    ci_hits = sum(1 for kw in CI_KEYWORDS if kw in haystack)
    so_hits = sum(1 for kw in SO_KEYWORDS if kw in haystack)
    if ci_hits == 0 and so_hits == 0:
        return None
    return "CI" if ci_hits >= so_hits else "SO"


def _extract_pdf_attachments(service, message_id: str) -> tuple[str, list[tuple[str, bytes]]]:
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    subject = ""
    for header in (msg.get("payload", {}) or {}).get("headers") or []:
        if str(header.get("name", "")).lower() == "subject":
            subject = header.get("value", "") or ""
            break

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
    return subject, attachments


def poll_for_user(user_id: int, workspace_id: str, max_messages: int = 15) -> dict[str, Any]:
    """Scan the connected Gmail inbox for CI/SO PDFs and import them.

    Must be called from inside a Flask request for this same authenticated
    user — it reuses that request's Authorization header/session to call the
    existing upload endpoints exactly as the founder's own device would.
    """
    import os
    import tempfile

    from flask import current_app, request
    from werkzeug.datastructures import FileStorage
    from centralized_db_system.db import CentralizedDB
    from app.routes.data import _upload_invoice_v2_impl, _db_path
    from app.three_step_verification import _extract_pdf_text

    db = CentralizedDB(_db_path())
    account = db.get_storage_account(
        user_id=user_id, provider_type="gmail", workspace_id=workspace_id
    )
    if not account or account.get("sync_status") != "connected" or not account.get("oauth_token"):
        raise RuntimeError("Gmail is not connected for this account.")

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
        "scanned": 0,
        "ci_imported": 0,
        "so_staged": 0,
        "skipped": 0,
        "errors": [],
    }

    auth_header = request.headers.get("Authorization")

    for m in messages:
        message_id = m["id"]
        if message_id in processed_ids:
            continue
        summary["scanned"] += 1
        try:
            subject, attachments = _extract_pdf_attachments(service, message_id)
            handled_any = False
            for filename, data in attachments:
                text_sample = ""
                tmp_path = None
                try:
                    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
                    with os.fdopen(fd, "wb") as tmp_f:
                        tmp_f.write(data)
                    text_sample = _extract_pdf_text(tmp_path)
                except Exception:
                    logger.exception("PDF text extraction failed for %s (message %s)", filename, message_id)
                finally:
                    if tmp_path:
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass

                kind = _classify_pdf(subject, filename, text_sample)
                if kind is None:
                    continue
                handled_any = True
                if kind == "CI":
                    fs = FileStorage(
                        stream=io.BytesIO(data), filename=filename, content_type="application/pdf"
                    )
                    resp = _upload_invoice_v2_impl(uploaded_file=fs)
                    payload = resp.get_json(silent=True) or {}
                    if payload.get("success"):
                        summary["ci_imported"] += 1
                    else:
                        summary["errors"].append(
                            {"message_id": message_id, "filename": filename, "error": payload.get("error")}
                        )
                else:
                    client = current_app.test_client()
                    headers = {"Authorization": auth_header} if auth_header else {}
                    resp = client.post(
                        "/api/v1/order-fulfillment/upload/sales-order",
                        data={"file": (io.BytesIO(data), filename)},
                        headers=headers,
                        content_type="multipart/form-data",
                    )
                    payload = resp.get_json(silent=True) or {}
                    if payload.get("success"):
                        summary["so_staged"] += 1
                    else:
                        summary["errors"].append(
                            {"message_id": message_id, "filename": filename, "error": payload.get("error")}
                        )
            if not handled_any:
                summary["skipped"] += 1
            db.mark_gmail_message_processed(
                user_id=user_id, workspace_id=workspace_id, message_id=message_id
            )
        except Exception as exc:
            logger.exception("Gmail CI/SO poll failed for message %s", message_id)
            summary["errors"].append({"message_id": message_id, "error": str(exc)})

    return summary

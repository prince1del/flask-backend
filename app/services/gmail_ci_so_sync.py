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
import re
from typing import Any

logger = logging.getLogger(__name__)

# Bombay Dyeing's own CI numbering scheme — every real one so far is a
# 10-digit number starting "140001" (e.g. 1400010167, 1400010223). Courier/
# shipment receipts also carry a real Indian GST (their own, on the
# consignee block), so the GST check alone can't tell them apart from a
# genuine CI — this pins CI acceptance to the actual numbering pattern too.
CI_NUMBER_PATTERN = re.compile(r"\b140001\d{4}\b")

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
    'newer_than:60d ('
    'has:attachment filename:pdf '
    '(subject:(invoice OR "sales order" OR "purchase order" OR "commercial invoice") '
    'OR filename:(invoice OR order)) '
    'OR "wetransfer.com" OR "we.tl"'
    ')'
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


def _extract_message_content(
    service, message_id: str
) -> tuple[str, str, str, list[tuple[str, bytes]]]:
    """Returns (subject, email_date_iso, body_text_for_link_scanning, [(filename, pdf_bytes), ...])."""
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
            import datetime

            email_date = datetime.datetime.fromtimestamp(
                int(internal_date_ms) / 1000, tz=datetime.timezone.utc
            ).isoformat()
        except (TypeError, ValueError):
            email_date = ""

    attachments: list[tuple[str, bytes]] = []
    body_chunks: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        filename = part.get("filename") or ""
        mime_type = str(part.get("mimeType") or "")
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
        elif not filename and mime_type in ("text/plain", "text/html") and body.get("data"):
            try:
                body_chunks.append(base64.urlsafe_b64decode(body["data"]).decode("utf-8", errors="ignore"))
            except Exception:
                pass
        for sub_part in part.get("parts") or []:
            walk(sub_part)

    walk(msg.get("payload") or {})
    return subject, email_date, "\n".join(body_chunks), attachments


def poll_for_user(
    user_id: int, workspace_id: str, max_messages: int = 15, reset_history: bool = False
) -> dict[str, Any]:
    """Scan the connected Gmail inbox for CI/SO PDFs and import them.

    Must be called from inside a Flask request for this same authenticated
    user — it reuses that request's Authorization header/session to call the
    existing upload endpoints exactly as the founder's own device would.
    """
    import json
    import os
    import tempfile

    from flask import current_app, request
    from werkzeug.datastructures import FileStorage
    from centralized_db_system.db import CentralizedDB
    from app.routes.data import (
        _upload_invoice_v2_impl,
        _expand_ci_upload_items,
        _so_pack_sniff_kind,
        _parse_sales_order_header_fields,
        _identify_buyer_gst,
        _extract_ci_buyer_gst,
        _auto_confirm_ci_preview,
    )
    from app.three_step_verification import _extract_pdf_text
    from app.services import wetransfer_fetch

    # Same no-arg CentralizedDB() as connect/status/disconnect in
    # app/routes/mail_sync.py — using data.py's _db_path() here instead
    # previously pointed at a different resolved DB file, so a freshly
    # connected Gmail account looked "not connected" the moment you polled.
    db = CentralizedDB()
    account = db.get_storage_account(
        user_id=user_id, provider_type="gmail", workspace_id=workspace_id
    )
    if not account or account.get("sync_status") != "connected" or not account.get("oauth_token"):
        raise RuntimeError("Gmail is not connected for this account.")

    if reset_history:
        db.clear_processed_gmail_messages(user_id=user_id, workspace_id=workspace_id)

    service = build_gmail_service(account["oauth_token"])
    processed_ids = db.get_processed_gmail_message_ids(user_id=user_id, workspace_id=workspace_id)

    own_profile = db.get_company_profile(workspace_id)
    own_gst = (own_profile or {}).get("gst_number")

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
        "imported_items": [],
        "pending_review": 0,
        "duplicates": 0,
        "debug": [],
    }

    auth_header = request.headers.get("Authorization")

    for m in messages:
        message_id = m["id"]
        if message_id in processed_ids:
            continue
        summary["scanned"] += 1
        try:
            subject, email_date, body_text, attachments = _extract_message_content(service, message_id)

            for link in wetransfer_fetch.find_links(f"{subject}\n{body_text}"):
                try:
                    fname, raw = wetransfer_fetch.fetch_transfer_bytes(link)
                    kind = _so_pack_sniff_kind(raw, fname)
                    if kind not in ("pdf", "zip", "rar"):
                        summary["errors"].append(
                            {"message_id": message_id, "link": link, "error": f"Unsupported file type: {kind}"}
                        )
                        continue
                    expanded = _expand_ci_upload_items([(fname, raw)])
                    attachments.extend(expanded)
                except Exception as exc:
                    logger.warning("WeTransfer fetch failed for %s (message %s): %s", link, message_id, exc)
                    summary["errors"].append({"message_id": message_id, "link": link, "error": str(exc)})

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

                # Keyword classification alone is too loose — subject/filename
                # words like "invoice" also match personal SaaS receipts
                # (Stripe, subscriptions, etc.), which then get uploaded as
                # if they were a real distributor CI/SO. Require the PDF to
                # carry a GSTIN at all (excluding our own company's) — real
                # Indian B2B tax documents always have one, personal/US SaaS
                # receipts never do. Deliberately NOT requiring it to match
                # an already-registered distributor: CI-only historical
                # sales (no prior SO/distributor record) are a supported
                # case in this app, and would wrongly get rejected by that
                # stricter check.
                header = _parse_sales_order_header_fields(text_sample)
                all_gsts = [g for g in (header.get("all_gst_numbers") or "").split(",") if g]
                buyer_gst = (
                    _extract_ci_buyer_gst(text_sample, own_gst)
                    if kind == "CI"
                    else _identify_buyer_gst(all_gsts, own_gst)
                )
                distributor_hit = (
                    db.get_master_distributor_by_gst(buyer_gst, workspace_id=workspace_id)
                    if buyer_gst else None
                )
                ci_number_hit = bool(CI_NUMBER_PATTERN.search(text_sample or ""))
                accepted = bool(buyer_gst) and (kind != "CI" or ci_number_hit)

                summary["debug"].append({
                    "message_id": message_id,
                    "subject": subject,
                    "filename": filename,
                    "classified_kind": kind,
                    "ci_number_pattern_matched": ci_number_hit,
                    "own_gst": own_gst,
                    "all_gsts_found": all_gsts,
                    "buyer_gst": buyer_gst,
                    "matched_known_distributor": bool(distributor_hit),
                    "distributor_name": (distributor_hit or {}).get("firm_name") if distributor_hit else None,
                    "accepted": accepted,
                })
                if not accepted:
                    continue

                handled_any = True
                if kind == "CI":
                    fs = FileStorage(
                        stream=io.BytesIO(data), filename=filename, content_type="application/pdf"
                    )
                    resp = _upload_invoice_v2_impl(uploaded_file=fs)
                    payload = resp.get_json(silent=True) or {}
                    if not payload.get("success"):
                        summary["errors"].append(
                            {"message_id": message_id, "filename": filename, "error": payload.get("error")}
                        )
                    else:
                        preview = payload.get("data") or {}
                        doc_no = preview.get("invoice_no") or preview.get("order_ref_no")
                        party_name = preview.get("buyer_name")
                        confirm_result = _auto_confirm_ci_preview(preview)
                        state = confirm_result.get("state")
                        if state == "ok":
                            summary["ci_imported"] += 1
                            summary["imported_items"].append({
                                "kind": "CI",
                                "filename": filename,
                                "email_date": email_date,
                                "doc_no": doc_no,
                                "party_name": party_name,
                                "is_duplicate": False,
                                "auto_confirmed": True,
                                "confirm_status": confirm_result.get("status"),
                            })
                        elif state == "dup":
                            summary["duplicates"] += 1
                        elif state == "review":
                            db.save_gmail_pending_import(
                                user_id=user_id,
                                workspace_id=workspace_id,
                                message_id=message_id,
                                kind="CI",
                                filename=filename,
                                doc_no=doc_no,
                                party_name=party_name,
                                reason=confirm_result.get("status"),
                                preview_json=json.dumps(preview, default=str),
                            )
                            summary["pending_review"] += 1
                        else:
                            summary["errors"].append({
                                "message_id": message_id,
                                "filename": filename,
                                "error": confirm_result.get("status"),
                            })
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
                    if not payload.get("success"):
                        summary["errors"].append(
                            {"message_id": message_id, "filename": filename, "error": payload.get("error")}
                        )
                    else:
                        preview = payload.get("data") or {}
                        doc_no = preview.get("order_ref_no") or preview.get("buyer_code")
                        party_name = preview.get("buyer_name")
                        matched_by_code = preview.get("matched_by_buyer_code") or {}
                        distributor_id = matched_by_code.get("id") if preview.get("signals_agree") else None
                        auto_confirmed = False
                        if distributor_id and preview.get("order_ref_no"):
                            confirm_resp = client.post(
                                "/api/v1/order-fulfillment/upload/sales-order",
                                data={
                                    "file": (io.BytesIO(data), filename),
                                    "distributor_id": str(distributor_id),
                                },
                                headers=headers,
                                content_type="multipart/form-data",
                            )
                            confirm_payload = confirm_resp.get_json(silent=True) or {}
                            confirm_data = confirm_payload.get("data") or {}
                            if (
                                confirm_payload.get("success")
                                and confirm_data.get("tracking_id")
                                and not confirm_data.get("is_duplicate")
                                and not confirm_data.get("link_error")
                            ):
                                auto_confirmed = True
                        if auto_confirmed:
                            summary["so_staged"] += 1
                            summary["imported_items"].append({
                                "kind": "SO",
                                "filename": filename,
                                "email_date": email_date,
                                "doc_no": doc_no,
                                "party_name": party_name,
                                "auto_confirmed": True,
                                "needs_confirmation": False,
                            })
                        elif preview.get("is_duplicate"):
                            summary["duplicates"] += 1
                        else:
                            db.save_gmail_pending_import(
                                user_id=user_id,
                                workspace_id=workspace_id,
                                message_id=message_id,
                                kind="SO",
                                filename=filename,
                                doc_no=doc_no,
                                party_name=party_name,
                                reason=(
                                    "Buyer code / GST signals don't agree — pick distributor"
                                    if preview.get("matched_by_buyer_code") or preview.get("matched_by_gst")
                                    else "No distributor match found — pick distributor"
                                ),
                                file_bytes=data,
                            )
                            summary["pending_review"] += 1
            if not handled_any:
                summary["skipped"] += 1
            db.mark_gmail_message_processed(
                user_id=user_id, workspace_id=workspace_id, message_id=message_id
            )
        except Exception as exc:
            logger.exception("Gmail CI/SO poll failed for message %s", message_id)
            summary["errors"].append({"message_id": message_id, "error": str(exc)})

    summary["imported_items"].sort(key=lambda item: item.get("email_date") or "", reverse=True)
    return summary

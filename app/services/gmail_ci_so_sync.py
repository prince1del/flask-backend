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
    "retail sale contract",
    "sale contract",
    "contract no",
    "we the undersigned",
    "shedule delivery",
    "schedule delivery",
)

MAIL_WINDOW_DAYS = 60

# Sales Orders arrive as often in a ZIP/RAR pack as they do as a loose PDF, so
# the search must not be restricted to `filename:pdf` — that alone made every
# zipped SO pack invisible to this poller.
GMAIL_QUERY = (
    f"newer_than:{MAIL_WINDOW_DAYS}d ("
    "has:attachment "
    '(subject:(invoice OR "sales order" OR "purchase order" OR "commercial invoice" '
    'OR order OR orders OR so OR rfa OR contract OR towel OR bedsheet OR bnd OR "retail sale") '
    'OR filename:(invoice OR order OR orders OR so OR rfa OR contract OR towel OR bedsheet OR bnd)) '
    'OR "wetransfer.com" OR "we.tl"'
    ")"
)

ATTACHMENT_EXTENSIONS = (".pdf", ".zip", ".rar")

GMAIL_READONLY_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def build_gmail_service(oauth_token: dict[str, Any]):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_info(oauth_token, GMAIL_READONLY_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


def _classify_pdf(
    subject: str,
    filename: str,
    text_sample: str,
    header: dict[str, str] | None = None,
) -> str | None:
    """Best-effort CI vs SO guess from subject/filename/PDF text. Returns
    None when neither looks like a match (email gets skipped, not imported)."""
    haystack = f"{subject}\n{filename}\n{text_sample}".lower()

    # Definite CI patterns (Bombay Dyeing CI 10-digit number starting 140001...)
    is_ci_number = bool(CI_NUMBER_PATTERN.search(text_sample or ""))
    is_ci_explicit = "commercial invoice" in haystack or ("invoice no" in haystack and is_ci_number)

    # Definite SO patterns (Bombay Dyeing Retail Sale Contract, PO, Contract No)
    is_so_explicit = (
        "retail sale contract" in haystack
        or "we the undersigned" in haystack
        or "shedule delivery" in haystack
        or "schedule delivery" in haystack
        or ("contract no" in haystack and not is_ci_number)
    )

    if is_so_explicit and not is_ci_number:
        return "SO"
    if is_ci_explicit:
        return "CI"

    ci_hits = sum(1 for kw in CI_KEYWORDS if kw in haystack)
    so_hits = sum(1 for kw in SO_KEYWORDS if kw in haystack)
    if ci_hits == 0 and so_hits == 0:
        return None
    # A real Sales Order prints an order reference and/or a buyer code, and it
    # never carries a Bombay Dyeing CI number. Structural fields beat keyword
    # counting: a genuine SO that happens to say "Invoice To"/"Tax Invoice"
    # anywhere on the page used to tip the keyword tie to CI, and was then
    # thrown away by the CI-number gate below — silently losing the SO.
    fields = header or {}
    looks_structurally_like_so = bool(
        fields.get("order_ref_no") or fields.get("buyer_code")
    )
    if looks_structurally_like_so and not is_ci_number:
        return "SO"
    return "CI" if ci_hits > so_hits else "SO"


def accept_document(
    kind: str,
    *,
    text_sample: str,
    header: dict[str, str],
    buyer_gst: str | None,
    all_gsts: list[str],
    ci_number_hit: bool,
) -> tuple[bool, str | None]:
    """Should this attachment be handed to the normal upload flow?

    Returns (accepted, reason_when_rejected). The bar must be no higher than
    the manual upload path a founder uses, otherwise mail sync silently drops
    documents the app would happily have accepted by hand.

    The anti-noise guard stays: a real Indian B2B document always prints a
    GSTIN somewhere, personal/US SaaS receipts never do. What it no longer
    does is demand a *uniquely identified* buyer GSTIN — `_identify_buyer_gst`
    returns None whenever the page shows anything other than exactly one
    non-own GSTIN (consignee, transporter, or simply no Company Profile GST on
    file), and requiring it here rejected practically every real Sales Order.
    Distributor matching is the upload endpoint's job, and it already falls
    back to Buyer Code and then to a human confirmation.
    """
    if not all_gsts:
        return False, "no_gstin_on_document"
    if kind == "CI":
        if not buyer_gst:
            return False, "ci_buyer_gst_not_identified"
        if not ci_number_hit:
            return False, "ci_number_pattern_not_matched"
        return True, None
    if not (buyer_gst or header.get("buyer_code") or header.get("order_ref_no")):
        return False, "so_has_no_order_ref_buyer_code_or_gst"
    return True, None


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
        if filename.lower().endswith(ATTACHMENT_EXTENSIONS) and body.get("attachmentId"):
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
        _db_path,
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

    # Must be the same database as the rest of the app (see `_db()` in
    # app/routes/mail_sync.py). An earlier fix aligned this with the *bare*
    # CentralizedDB() used by connect/status, which cured the "looks not
    # connected" symptom by moving the poller onto a file that has no
    # distributors, no company profile and no Order Desk — so it then found
    # and imported nothing. Both sides now resolve DATABASE_PATH like
    # app/routes/data.py does.
    db = CentralizedDB(_db_path())
    account = db.get_storage_account(
        user_id=user_id, provider_type="gmail", workspace_id=workspace_id
    )
    if not account or account.get("sync_status") != "connected" or not account.get("oauth_token"):
        # Not an error the user should meet as a bare failure — it is the most
        # common reason "nothing came from email", and it is fixable by him.
        return _finish(
            db,
            user_id=user_id,
            workspace_id=workspace_id,
            summary={
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
            },
        )

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
        # Everything below exists so "0 CI, 0 SO" can never again mean four
        # different things the user cannot tell apart.
        "messages_matched": len(messages),
        "attachments_seen": 0,
        "attachments_unreadable": 0,
        "unreadable_files": [],
        "skipped_reasons": [],
        "window_days": MAIL_WINDOW_DAYS,
    }

    auth_header = request.headers.get("Authorization")

    for m in messages:
        message_id = m["id"]
        if message_id in processed_ids:
            continue
        summary["scanned"] += 1
        try:
            subject, email_date, body_text, raw_attachments = _extract_message_content(service, message_id)
            attachments: list[tuple[str, bytes, str]] = []
            for fn, data in raw_attachments:
                kind_sniff = _so_pack_sniff_kind(data, fn)
                if kind_sniff == "pdf":
                    attachments.append((fn, data, "gmail_attachment"))
                    continue
                if kind_sniff in ("zip", "rar"):
                    # A zipped SO pack is the normal shape for Sales Orders —
                    # unpack it with the very same expander the manual CI/SO
                    # upload uses.
                    try:
                        for inner_name, inner_data in _expand_ci_upload_items([(fn, data)]):
                            attachments.append((inner_name, inner_data, "gmail_attachment"))
                    except Exception as exc:
                        summary["attachments_seen"] += 1
                        summary["attachments_unreadable"] += 1
                        summary["unreadable_files"].append({
                            "message_id": message_id,
                            "subject": subject,
                            "filename": fn,
                            "reason": str(exc),
                        })
                    continue
                summary["attachments_seen"] += 1
                summary["attachments_unreadable"] += 1
                summary["unreadable_files"].append({
                    "message_id": message_id,
                    "subject": subject,
                    "filename": fn,
                    "reason": "unsupported_file_type",
                })

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
                    attachments.extend((fn, data, "wetransfer") for fn, data in expanded)
                except Exception as exc:
                    logger.warning("WeTransfer fetch failed for %s (message %s): %s", link, message_id, exc)
                    summary["errors"].append({"message_id": message_id, "link": link, "error": str(exc)})

            handled_any = False
            for filename, data, source in attachments:
                summary["attachments_seen"] += 1
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

                header = _parse_sales_order_header_fields(text_sample)

                if not (text_sample or "").strip():
                    # A scanned/photographed PDF has no text layer at all, so
                    # nothing downstream can read it. Say which file, and why.
                    summary["attachments_unreadable"] += 1
                    summary["unreadable_files"].append({
                        "message_id": message_id,
                        "subject": subject,
                        "filename": filename,
                        "reason": "no_text_layer",
                    })
                    continue

                kind = _classify_pdf(subject, filename, text_sample, header)
                if kind is None:
                    summary["skipped_reasons"].append({
                        "message_id": message_id,
                        "subject": subject,
                        "filename": filename,
                        "reason": "not_a_sales_order_or_invoice",
                    })
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
                all_gsts = [g for g in (header.get("all_gst_numbers") or "").split(",") if g]
                buyer_gst = (
                    _extract_ci_buyer_gst(text_sample, own_gst)
                    if kind == "CI"
                    else _identify_buyer_gst(all_gsts, own_gst)
                )
                distributor_hit = (
                    db.get_master_distributor_by_gst(
                        buyer_gst, workspace_id=workspace_id, user_id=user_id
                    )
                    if buyer_gst
                    else None
                )
                if not distributor_hit and header.get("buyer_code"):
                    distributor_hit = db.get_master_distributor_by_buyer_code(
                        header.get("buyer_code"),
                        workspace_id=workspace_id,
                        user_id=user_id,
                    )
                ci_number_hit = bool(CI_NUMBER_PATTERN.search(text_sample or ""))
                accepted, reject_reason = accept_document(
                    kind,
                    text_sample=text_sample,
                    header=header,
                    buyer_gst=buyer_gst,
                    all_gsts=all_gsts,
                    ci_number_hit=ci_number_hit,
                )

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
                    "reject_reason": reject_reason,
                })
                if not accepted:
                    summary["skipped_reasons"].append({
                        "message_id": message_id,
                        "subject": subject,
                        "filename": filename,
                        "reason": reject_reason,
                    })
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
                        db.log_gmail_import(
                            user_id=user_id, workspace_id=workspace_id, message_id=message_id,
                            source=source, kind="CI", filename=filename, doc_no=None, party_name=None,
                            outcome="error", detail=str(payload.get("error")), email_date=email_date,
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
                            db.log_gmail_import(
                                user_id=user_id, workspace_id=workspace_id, message_id=message_id,
                                source=source, kind="CI", filename=filename, doc_no=doc_no, party_name=party_name,
                                outcome="auto_confirmed", tracking_id=confirm_result.get("tracking_id"),
                                detail=confirm_result.get("status"), email_date=email_date,
                            )
                        elif state == "dup":
                            summary["duplicates"] += 1
                            db.log_gmail_import(
                                user_id=user_id, workspace_id=workspace_id, message_id=message_id,
                                source=source, kind="CI", filename=filename, doc_no=doc_no, party_name=party_name,
                                outcome="duplicate", detail=confirm_result.get("status"), email_date=email_date,
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
                                reason=confirm_result.get("status"),
                                preview_json=json.dumps(preview, default=str),
                            )
                            summary["pending_review"] += 1
                            db.log_gmail_import(
                                user_id=user_id, workspace_id=workspace_id, message_id=message_id,
                                source=source, kind="CI", filename=filename, doc_no=doc_no, party_name=party_name,
                                outcome="pending_review", detail=confirm_result.get("status"), email_date=email_date,
                            )
                        else:
                            summary["errors"].append({
                                "message_id": message_id,
                                "filename": filename,
                                "error": confirm_result.get("status"),
                            })
                            db.log_gmail_import(
                                user_id=user_id, workspace_id=workspace_id, message_id=message_id,
                                source=source, kind="CI", filename=filename, doc_no=doc_no, party_name=party_name,
                                outcome="error", detail=confirm_result.get("status"), email_date=email_date,
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
                    if not payload.get("success"):
                        summary["errors"].append(
                            {"message_id": message_id, "filename": filename, "error": payload.get("error")}
                        )
                        db.log_gmail_import(
                            user_id=user_id, workspace_id=workspace_id, message_id=message_id,
                            source=source, kind="SO", filename=filename, doc_no=None, party_name=None,
                            outcome="error", detail=str(payload.get("error")), email_date=email_date,
                        )
                    else:
                        preview = payload.get("data") or {}
                        doc_no = preview.get("order_ref_no") or preview.get("buyer_code")
                        party_name = preview.get("buyer_name")
                        matched_by_code = preview.get("matched_by_buyer_code") or {}
                        matched_by_gst = preview.get("matched_by_gst") or {}
                        # Auto-confirm only when the signals cannot disagree:
                        # both present and agreeing, or exactly one present.
                        # Previously this required `signals_agree`, which is
                        # None unless BOTH matched — so an SO identified by
                        # buyer code alone (the common case) was never
                        # auto-imported.
                        # Smart Confidence Matching:
                        # 1. Exact buyer_code or buyer_gst match
                        # 2. Strong buyer_name fuzzy match to distributor firm_name
                        distributor_id = None
                        suggested_distributor_id = None
                        suggested_distributor_name = None
                        suggested_confidence_pct = None

                        if preview.get("signals_agree") is True:
                            distributor_id = matched_by_code.get("id")
                            suggested_confidence_pct = 99
                        elif matched_by_code and not matched_by_gst:
                            distributor_id = matched_by_code.get("id")
                            suggested_confidence_pct = 95
                        elif matched_by_gst and not matched_by_code:
                            distributor_id = matched_by_gst.get("id")
                            suggested_confidence_pct = 90
                        elif distributor_hit:
                            distributor_id = distributor_hit.get("id")
                            suggested_confidence_pct = 92
                        elif matched_by_code and matched_by_gst and matched_by_code.get("id") != matched_by_gst.get("id"):
                            # Ambiguous signals: buyer code points to one, GST to another.
                            # Check buyer_name to break tie with high confidence!
                            dist_code_name = (matched_by_code.get("firm_name") or "").lower()
                            dist_gst_name = (matched_by_gst.get("firm_name") or "").lower()
                            raw_pname = (party_name or "").lower()

                            from rapidfuzz import fuzz
                            code_score = fuzz.token_set_ratio(raw_pname, dist_code_name) if raw_pname and dist_code_name else 0
                            gst_score = fuzz.token_set_ratio(raw_pname, dist_gst_name) if raw_pname and dist_gst_name else 0

                            if code_score >= 80:
                                distributor_id = matched_by_code.get("id")
                                suggested_confidence_pct = max(90, code_score)
                            elif gst_score >= 80:
                                distributor_id = matched_by_gst.get("id")
                                suggested_confidence_pct = max(90, gst_score)
                            elif code_score >= 60 and code_score > gst_score + 15:
                                distributor_id = matched_by_code.get("id")
                                suggested_confidence_pct = max(88, code_score)
                            elif gst_score >= 60 and gst_score > code_score + 15:
                                distributor_id = matched_by_gst.get("id")
                                suggested_confidence_pct = max(88, gst_score)
                            else:
                                # Pick higher as suggestion
                                if code_score >= gst_score:
                                    suggested_distributor_id = matched_by_code.get("id")
                                    suggested_distributor_name = matched_by_code.get("firm_name")
                                    suggested_confidence_pct = code_score
                                else:
                                    suggested_distributor_id = matched_by_gst.get("id")
                                    suggested_distributor_name = matched_by_gst.get("firm_name")
                                    suggested_confidence_pct = gst_score

                        if not distributor_id and not suggested_distributor_id and party_name:
                            # Fuzzy match against all known distributors for this user
                            known_dists = db.list_master_distributors(
                                limit=100, workspace_id=workspace_id, user_id=user_id
                            )
                            from rapidfuzz import fuzz
                            best_d = None
                            best_s = 0
                            for d in known_dists:
                                fn = (d.get("firm_name") or d.get("name") or "").lower()
                                s = fuzz.token_set_ratio(party_name.lower(), fn)
                                if s > best_s:
                                    best_s = s
                                    best_d = d
                            if best_d and best_s >= 88:
                                distributor_id = best_d.get("id")
                                suggested_confidence_pct = best_s
                            elif best_d and best_s >= 60:
                                suggested_distributor_id = best_d.get("id")
                                suggested_distributor_name = best_d.get("firm_name") or best_d.get("name")
                                suggested_confidence_pct = best_s

                        auto_confirmed = False
                        confirmed_tracking_id = None
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
                                confirmed_tracking_id = confirm_data.get("tracking_id")
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
                            db.log_gmail_import(
                                user_id=user_id, workspace_id=workspace_id, message_id=message_id,
                                source=source, kind="SO", filename=filename, doc_no=doc_no, party_name=party_name,
                                outcome="auto_confirmed", tracking_id=confirmed_tracking_id, email_date=email_date,
                            )
                        elif preview.get("is_duplicate"):
                            summary["duplicates"] += 1
                            db.log_gmail_import(
                                user_id=user_id, workspace_id=workspace_id, message_id=message_id,
                                source=source, kind="SO", filename=filename, doc_no=doc_no, party_name=party_name,
                                outcome="duplicate", email_date=email_date,
                            )
                        else:
                            reason = (
                                "Buyer code / GST signals don't agree — pick distributor"
                                if preview.get("matched_by_buyer_code") or preview.get("matched_by_gst")
                                else "No distributor match found — pick distributor"
                            )
                            if preview.get("link_error"):
                                reason = str(preview.get("link_error"))
                            elif preview.get("is_duplicate"):
                                reason = "This Sales Order is already in the system"
                            import json as _json
                            meta_preview = {
                                "suggested_distributor_id": suggested_distributor_id,
                                "suggested_distributor_name": suggested_distributor_name,
                                "suggested_confidence_pct": suggested_confidence_pct,
                                "buyer_code": preview.get("buyer_code") or header.get("buyer_code"),
                                "buyer_name": preview.get("buyer_name") or header.get("buyer_name") or party_name,
                                "buyer_gst": preview.get("buyer_gst") or buyer_gst,
                                "order_ref_no": doc_no,
                            }
                            db.save_gmail_pending_import(
                                user_id=user_id,
                                workspace_id=workspace_id,
                                message_id=message_id,
                                kind="SO",
                                filename=filename,
                                doc_no=doc_no,
                                party_name=party_name,
                                reason=reason,
                                preview_json=_json.dumps(meta_preview),
                                file_bytes=data,
                            )
                            summary["pending_review"] += 1
                            db.log_gmail_import(
                                user_id=user_id, workspace_id=workspace_id, message_id=message_id,
                                source=source, kind="SO", filename=filename, doc_no=doc_no, party_name=party_name,
                                outcome="pending_review", detail=reason, email_date=email_date,
                            )
            if not handled_any:
                summary["skipped"] += 1
            db.mark_gmail_message_processed(
                user_id=user_id, workspace_id=workspace_id, message_id=message_id
            )
        except Exception as exc:
            logger.exception("Gmail CI/SO poll failed for message %s", message_id)
            summary["errors"].append({"message_id": message_id, "error": str(exc)})

    summary["imported_items"].sort(key=lambda item: item.get("email_date") or "", reverse=True)
    return _finish(db, user_id=user_id, workspace_id=workspace_id, summary=summary)


def _finish(db, *, user_id: int, workspace_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    """Attach the plain-language outcome and leave a support record behind.

    Same contract as the SO pack upload diagnostics: the outcome code is the
    app's i18n key, rows are written per `user_id`, and a clean import writes
    nothing at all.
    """
    import sqlite3

    from app.services import mail_sync_diagnostics as diag

    assessment = diag.assess(summary)
    summary["outcome"] = assessment["outcome"]
    summary["message"] = diag.message_for(assessment)
    summary["diagnosis"] = assessment

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

"""Backup distributor payment receiving ledgers to Drive/NEXORA/Payment Receiving.

Structured payment rows have no uploaded file — we export a JSON snapshot
after each create/delete (best-effort, never fails the API call).
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PAYMENT_RECEIVING_SUBFOLDER = "Payment Receiving"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H%M%S")


def _safe_stem(label: str | None) -> str:
    text = (label or "nexora").strip()
    for ch in '<>:"/\\|?*':
        text = text.replace(ch, " ")
    return " ".join(text.split()) or "nexora"


def _write_json_snapshot(payload: dict[str, Any]) -> Path | None:
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            delete=False,
            prefix="nexora_payment_",
        )
        with tmp:
            json.dump(payload, tmp, ensure_ascii=False, indent=2, default=str)
            tmp.flush()
        return Path(tmp.name)
    except OSError:
        logger.exception("Could not write payment backup temp file")
        return None


def _push_snapshot(
    *,
    user_id: int | None,
    workspace_id: str | None,
    local_path: Path,
    display_name: str,
) -> None:
    try:
        from app.storage.nexora_docs import push_file_to_nexora_drive

        push_file_to_nexora_drive(
            user_id=user_id,
            workspace_id=workspace_id,
            local_path=local_path,
            subfolder=PAYMENT_RECEIVING_SUBFOLDER,
            display_name=display_name,
        )
    finally:
        try:
            local_path.unlink(missing_ok=True)
        except OSError:
            pass


def backup_so_payment_collection_to_drive(
    *,
    db: Any,
    user_id: int | None,
    workspace_id: str | None,
    username: str | None = None,
) -> None:
    """Order Desk → Distributor's Payment Collection (SO-wise deposits)."""
    if not user_id or not workspace_id:
        return
    try:
        distributors = db.list_distributor_payment_collection(
            workspace_id, user_id=user_id,
        )
        so_bill_total = round(sum(float(d.get("so_bill_total") or 0) for d in distributors), 2)
        paid_total = round(sum(float(d.get("paid_total") or 0) for d in distributors), 2)
        outstanding_total = round(
            sum(float(d.get("outstanding_total") or 0) for d in distributors), 2,
        )
        payload = {
            "kind": "so_wise_payment_collection",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "workspace_id": workspace_id,
            "user_id": user_id,
            "summary": {
                "distributor_count": len(distributors),
                "so_bill_total": so_bill_total,
                "paid_total": paid_total,
                "outstanding_total": outstanding_total,
            },
            "distributors": distributors,
        }
        path = _write_json_snapshot(payload)
        if not path:
            return
        name = f"{_safe_stem(username)} SO Payment Receiving {_stamp()}.json"
        _push_snapshot(
            user_id=user_id,
            workspace_id=workspace_id,
            local_path=path,
            display_name=name,
        )
    except Exception:
        logger.exception("SO payment receiving Drive backup failed")


def backup_category_payment_status_to_drive(
    *,
    db: Any,
    user_id: int | None,
    workspace_id: str | None,
    username: str | None = None,
) -> None:
    """Drawer → Distributors Payment Status (season/category deposits)."""
    if not user_id:
        return
    try:
        distributors = db.list_distributor_category_payment_status(user_id)
        payload = {
            "kind": "distributor_category_payment_status",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "workspace_id": workspace_id,
            "user_id": user_id,
            "distributors": distributors,
        }
        path = _write_json_snapshot(payload)
        if not path:
            return
        name = f"{_safe_stem(username)} Distributor Payment Status {_stamp()}.json"
        _push_snapshot(
            user_id=user_id,
            workspace_id=workspace_id,
            local_path=path,
            display_name=name,
        )
    except Exception:
        logger.exception("Category payment status Drive backup failed")

"""Automatic matching of incoming Sales Orders (Mail Sync / Uploads) to Saved Filled Orders.

When an SO is uploaded or auto-imported from Gmail:
1. Detects category (Bath/Towel vs Bed) and Season (AW26, SS26, etc.).
2. Finds the matching Filled Order for that distributor + category + season.
3. Automatically creates or updates the FO ↔ SO Match Run so it shows up in
   Order Desk's Sales Orders tab without manual 'Analyze SO Pack' clicks.
"""

from __future__ import annotations

import io
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def infer_so_category_and_season(pack: dict[str, Any]) -> tuple[list[str], str | None]:
    """Inspects parsed SO pack lines to infer category candidates and season."""
    lines = pack.get("line_detail") or []
    meta = pack.get("meta") or {}

    is_bath = False
    is_bed = False

    for line in lines:
        if not isinstance(line, dict):
            continue
        pname = str(line.get("product_name") or "").upper()
        pdetail = str(line.get("product_detail") or "").upper()
        mcode = str(line.get("material_code") or "").upper()
        combined = f"{pname} {pdetail} {mcode}"

        if (
            mcode.startswith("MT")
            or any(
                w in combined
                for w in (
                    "TOWEL",
                    "BATH",
                    "NATURES BQT",
                    "NATURE'S BQT",
                    "SANTINO",
                    "FLORA",
                    "NAPKIN",
                    "FACE TOWEL",
                    "HAND TOWEL",
                    "BATH MAT",
                    "BATHROBE",
                )
            )
        ):
            is_bath = True
            break
        elif (
            mcode.startswith("MB")
            or any(
                w in combined
                for w in (
                    "BED",
                    "BEDSHEET",
                    "FST",
                    "SHEET",
                    "DOHAR",
                    "COMFORTER",
                    "PILLOW",
                    "FITTED SHEET",
                    "TOP SHEET",
                    "DUVET",
                )
            )
        ):
            is_bed = True

    if is_bath:
        category_candidates = ["Bath", "Towel", "Bath linen", "bath", "towel"]
    elif is_bed:
        category_candidates = ["Bed", "Bedsheet", "Bed linen", "bed", "bedsheet"]
    else:
        category_candidates = ["Bath", "Bed", "Towel", "Bedsheet"]

    season = meta.get("season")
    if not season:
        for line in lines:
            if not isinstance(line, dict):
                continue
            txt = f"{line.get('product_name', '')} {line.get('product_detail', '')}".upper()
            import re
            m = re.search(r"\b(AW\d{2}|SS\d{2})\b", txt)
            if m:
                season = m.group(1)
                break

    if not season:
        from app.fiscal_year import season_from_date
        order_date = meta.get("order_date") or meta.get("contract_date")
        season = season_from_date(order_date)

    return category_candidates, season


def find_matching_filled_order(
    conn: sqlite3.Connection,
    user_id: int,
    distributor_id: int,
    category_candidates: list[str],
    season: str | None,
) -> dict[str, Any] | None:
    """Finds the best matching Filled Order for the given distributor, category, and season."""
    import filled_orders_db as fodb

    fodb.ensure_schema(conn)

    # 1. Try exact (distributor, category, season) match
    if season:
        for cat in category_candidates:
            fo = fodb.find_filled_order_by_distributor_category_season(
                conn, user_id, distributor_id, cat, season
            )
            if fo:
                return fo

    # 2. Try matching by category alone (latest season for that category)
    for cat in category_candidates:
        fo_list = fodb.list_filled_orders(
            conn, user_id, distributor_id=distributor_id, category=cat
        )
        if fo_list:
            return fo_list[0]

    # 3. Fallback to latest filled order for this distributor
    return fodb.get_latest_filled_order(conn, user_id, distributor_id, season=season)


def auto_attach_so_to_filled_order(
    conn: sqlite3.Connection,
    user_id: int,
    distributor_id: int,
    file_bytes: bytes | None = None,
    file_path: str | Path | None = None,
    filename: str = "sales_order.pdf",
    tracking_id: int | None = None,
    pre_analyzed_pack: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Auto-parses and matches an SO to its corresponding saved Filled Order."""
    if not user_id or not distributor_id:
        return None

    try:
        pack = pre_analyzed_pack
        if pack is None:
            if file_bytes is None and file_path is not None:
                p = Path(file_path)
                if p.exists():
                    file_bytes = p.read_bytes()

            if not file_bytes:
                return None

            from app.services.so_pack_consolidate import analyze_so_pack

            pack = analyze_so_pack(file_bytes, filename)

        from app.routes.data import _so_pack_usable_lines

        if not pack or not _so_pack_usable_lines(pack):
            return None

        lines = [r for r in (pack.get("line_detail") or []) if isinstance(r, dict)]
        if not lines:
            return None

        category_candidates, season = infer_so_category_and_season(pack)

        fo = find_matching_filled_order(
            conn, user_id, distributor_id, category_candidates, season
        )
        if not fo:
            logger.warning(
                "No matching filled order found for distributor %s, categories %s, season %s",
                distributor_id,
                category_candidates,
                season,
            )
            return None

        fo_id = int(fo["id"])
        import filled_orders_db as fodb
        from app.services import fo_so_match_db as matchdb
        from app.services import fo_so_revision as sorev
        from app.services.fo_so_match_lab import run_match_saved_fo_vs_so_pack

        items = fodb.get_filled_order_items(conn, fo_id)
        if not items:
            return None

        def _link_now() -> None:
            # Only ever called after a match genuinely succeeded (or was
            # confirmed already-matched) below — linking BEFORE that point
            # used to mean a mid-match exception still permanently marked
            # this tracking_id "linked" (filled_order_so_link is an
            # unconditional INSERT ... ON CONFLICT DO NOTHING with an
            # immediate commit), so a failed attempt could never be
            # retried: list_candidate_sales_orders_for_filled_order
            # excludes anything already in that table.
            if tracking_id:
                try:
                    fodb.link_filled_order_to_tracking(conn, fo_id, tracking_id)
                except Exception:
                    pass

        existing = sorev.get_latest_run_for_fo(
            conn, user_id=user_id, filled_order_id=fo_id
        )
        if existing:
            new_numbers = matchdb.extract_so_numbers_from_pack(pack)
            conflicts = matchdb.find_so_number_conflicts(
                conn, so_numbers=new_numbers
            )
            conflicts = [c for c in conflicts if c.get("filled_order_id") == fo_id]
            decision = sorev.analyze_incoming_against_existing(
                existing_run=existing, so_pack=pack, conflicts=conflicts
            )
            action = decision.get("action")
            existing_lines = list(existing.get("so_line_detail") or [])
            new_lines = lines

            if action == "already_in_system":
                if sorev.run_reflects_so_lines(existing, lines):
                    _link_now()
                    return {"status": "already_matched", "run_id": existing.get("id")}
                merged = new_lines
            elif action == "replace":
                replace_nums = {
                    str(c.get("so_number") or "") for c in conflicts if c.get("so_number")
                } or set(new_numbers)
                merged = sorev.merge_lines_for_replace(
                    existing_lines, new_lines, replace_nums
                )
            else:  # additional, split, or new (save_new)
                merged = sorev.merge_lines_for_additional(existing_lines, new_lines)

            working_pack = sorev.pack_from_lines(merged, source_filename=filename)
            result = run_match_saved_fo_vs_so_pack(
                fo_meta=fo, fo_items=items, so_pack_payload=working_pack
            )
            working_lines = (
                working_pack.get("line_detail")
                if isinstance(working_pack.get("line_detail"), list)
                else None
            )
            run = matchdb.update_run_from_match(
                conn,
                run_id=int(existing["id"]),
                user_id=user_id,
                match_payload=result,
                so_line_detail=working_lines,
                so_pack=working_pack,
                so_source_filename=filename,
            )
            logger.warning(
                "Auto-updated match run %s for FO %s (%s)",
                run.get("id"),
                fo_id,
                fo.get("category"),
            )
            _link_now()
            return {"status": "updated", "run_id": run.get("id")}
        else:
            result = run_match_saved_fo_vs_so_pack(
                fo_meta=fo, fo_items=items, so_pack_payload=pack
            )
            working_lines = (
                pack.get("line_detail")
                if isinstance(pack.get("line_detail"), list)
                else None
            )
            run = matchdb.save_match_run(
                conn,
                user_id=user_id,
                match_payload=result,
                so_buyer_label=fo.get("distributor_name"),
                so_source_filename=filename,
                so_line_detail=working_lines,
                so_pack=pack,
            )
            logger.warning(
                "Auto-created match run %s for FO %s (%s)",
                run.get("id"),
                fo_id,
                fo.get("category"),
            )
            _link_now()
            return {"status": "created", "run_id": run.get("id")}
    except Exception as exc:
        logger.warning("Error auto-attaching SO to filled order: %s", exc, exc_info=True)
        return None


def auto_sync_all_unmatched_sos_for_user(
    conn: sqlite3.Connection,
    user_id: int,
    workspace_id: str = "default",
) -> int:
    """Scans all saved filled orders for user_id that lack a match run or have pending SOs,
    and automatically matches them from tracked SO files."""
    import filled_orders_db as fodb

    fodb.ensure_schema(conn)
    all_fos = fodb.list_filled_orders(conn, user_id=user_id)
    matched_count = 0
    # Temporary step-by-step trace — the last three self-heal fixes each
    # left matched=0 with no exception, so guessing at the next hypothesis
    # blind isn't good enough; this pins down exactly which stage empties
    # out for this user/workspace next time the logs are pulled.
    logger.warning(
        "auto_sync_all_unmatched_sos_for_user: user_id=%s workspace_id=%r fo_count=%s",
        user_id, workspace_id, len(all_fos),
    )

    for fo in all_fos:
        fo_id = int(fo["id"])
        dist_id = fo.get("distributor_id")
        if not dist_id:
            logger.warning("  fo_id=%s skipped: no distributor_id", fo_id)
            continue

        # Every candidate is tried regardless of whether this FO already has
        # a match run with lines — a partially-matched FO (e.g. 24 SOs
        # matched the normal way) still needs to pick up SOs that arrived
        # later (mail-sync) and never got attached. auto_attach_so_to_filled_order
        # already no-ops safely ("already_matched") for an SO number that's
        # already reflected in the existing run, so re-trying every
        # candidate every time is safe, not just for brand-new FOs.
        candidates = fodb.list_candidate_sales_orders_for_filled_order(
            conn, fo_id, workspace_id
        )
        logger.warning(
            "  fo_id=%s distributor_id=%s category=%s season=%s candidates=%s",
            fo_id, dist_id, fo.get("category"), fo.get("season"), len(candidates),
        )
        for cand in candidates:
            tid = cand.get("tracking_id")
            so_ref = cand.get("sales_order_file_reference")
            if not so_ref:
                logger.warning("    tracking_id=%s skipped: no sales_order_file_reference", tid)
                continue
            path = Path(so_ref)
            file_bytes = None
            filename = path.name
            if not path.exists():
                # The local upload path is on the web dyno's ephemeral disk
                # and does not survive a redeploy — fall back to the durable
                # Drive copy (same source download_order_fulfillment_tracking_file
                # in app/routes/data.py already uses for "SO/CI PDF: Google
                # Drive first, then local upload file").
                drive_file_id = cand.get("sales_order_drive_file_id")
                if not drive_file_id:
                    logger.warning(
                        "    tracking_id=%s skipped: local file missing (%s) and no drive_file_id",
                        tid, so_ref,
                    )
                    continue
                try:
                    from app.storage.manager import StorageManager
                    from app.storage.providers.google_drive_provider import GoogleDriveProvider

                    manager = StorageManager()
                    manager.register_provider("google_drive", GoogleDriveProvider)
                    payload = manager.download_file_bytes(
                        user_id=user_id, file_id=drive_file_id, workspace_id=workspace_id
                    )
                    file_bytes = payload.get("content")
                    filename = payload.get("file_name") or filename
                except Exception as exc:
                    logger.warning(
                        "Drive fallback download failed for tracking %s: %s",
                        cand.get("tracking_id"), exc,
                    )
                    continue
                if not file_bytes:
                    logger.warning("    tracking_id=%s skipped: drive download returned no bytes", tid)
                    continue

            res = auto_attach_so_to_filled_order(
                conn=conn,
                user_id=user_id,
                distributor_id=int(dist_id),
                file_path=path if file_bytes is None else None,
                file_bytes=file_bytes,
                filename=filename,
                tracking_id=cand.get("tracking_id"),
            )
            logger.warning("    tracking_id=%s attach result=%s", tid, res)
            if res and res.get("status") in ("created", "updated"):
                matched_count += 1

    return matched_count

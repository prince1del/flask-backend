"""Tests for the visible record of automatic FO <-> SO match decisions.

The point of this log is that automation stops being a black box. The
most important rows are the refusals: after the 2026-08-28 fix that makes
auto-attach refuse rather than guess, an SO that cannot be placed simply
never appears anywhere — the user must be able to see that it happened
and why, or "my SO is missing" becomes unanswerable again.
"""

import sqlite3
import tempfile

import filled_orders_db as fodb
from app.services import fo_so_auto_match as auto_match
from app.services import fo_so_auto_match_log as matchlog
from app.services import fo_so_match_db as matchdb


def _setup_db():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    conn = sqlite3.connect(path)
    fodb.ensure_schema(conn)
    matchdb.ensure_schema(conn)
    matchlog.ensure_schema(conn)
    return conn, path


def _bath_pack(so_number="102876566"):
    return {
        "line_detail": [{
            "so_number": so_number,
            "product_name": "RIMZIM COOLTEX BATH TOWEL",
            "product_detail": "RIMZIM COOLTEX BATH TOWEL DYED 1 DESIGN",
            "material_code": "MT99999",
            "qty": 360,
            "net_amount": 61920,
        }],
        "meta": {"order_date": "18.08.2026", "source_filename": "bath_so.pdf"},
    }


def _bed_fo(conn, user_id, dist_id, *, with_item=True):
    fo_id = fodb.create_filled_order(
        conn=conn, user_id=user_id, distributor_id=dist_id,
        distributor_name_raw="Shri Ram & Co", category="Bed", season="AW26",
    )
    if with_item:
        fodb.insert_filled_order_item(
            conn, fo_id,
            {
                "item_key": "aster_db_bs", "brand": "Aster", "size": "DB BS",
                "product_type": "Bedsheet", "raw_qty_value": 432, "detected_unit": "pcs",
                "final_piece_qty": 432, "is_clean_bale_multiple": True, "matched": True,
                "mrp": 2000, "ptr": 1500, "ex_mill_price": 1200,
            },
        )
    return fo_id


def test_category_mismatch_refusal_is_logged_and_flagged():
    """The exact Shri Ram & Co case: a Bath SO against a Bed-only FO is
    refused — and that refusal must be visible, not silent."""
    conn, _ = _setup_db()
    try:
        user_id, dist_id = 1, 101
        bed_fo_id = _bed_fo(conn, user_id, dist_id)

        res = auto_match.auto_attach_so_to_filled_order(
            conn=conn, user_id=user_id, distributor_id=dist_id,
            filename="bath_so.pdf", pre_analyzed_pack=_bath_pack(),
            tracking_id=555, expected_fo_id=bed_fo_id,
            workspace_id="bombay_dyeing_gt_north", source="self_heal",
        )
        assert res is None

        entries = matchlog.list_decisions(
            conn, user_id=user_id, workspace_id="bombay_dyeing_gt_north"
        )
        assert len(entries) == 1
        entry = entries[0]
        assert entry["outcome"] == matchlog.OUTCOME_SKIPPED_CATEGORY_MISMATCH
        assert entry["needs_attention"] is True
        assert entry["tracking_id"] == 555
        assert entry["source"] == "self_heal"
        assert "102876566" in (entry["so_numbers"] or "")
        # The reason must be readable by the person, not just a code.
        assert "Bath" in entry["detail"] and "Bed" in entry["detail"]

        assert matchlog.count_needs_attention(
            conn, user_id=user_id, workspace_id="bombay_dyeing_gt_north"
        ) == 1
    finally:
        conn.close()


def test_no_matching_fo_refusal_is_logged():
    """Confidently-Bath SO, distributor has no Bath FO at all."""
    conn, _ = _setup_db()
    try:
        user_id, dist_id = 1, 101
        _bed_fo(conn, user_id, dist_id)

        res = auto_match.auto_attach_so_to_filled_order(
            conn=conn, user_id=user_id, distributor_id=dist_id,
            filename="bath_so.pdf", pre_analyzed_pack=_bath_pack(),
            tracking_id=556, workspace_id="ws", source="so_upload",
        )
        assert res is None

        entries = matchlog.list_decisions(conn, user_id=user_id, workspace_id="ws")
        assert [e["outcome"] for e in entries] == [matchlog.OUTCOME_SKIPPED_NO_FO]
        assert entries[0]["needs_attention"] is True
        # Tells the user what to actually do about it.
        assert "Filled Order" in entries[0]["detail"]
    finally:
        conn.close()


def test_successful_match_is_logged_without_attention_flag():
    conn, _ = _setup_db()
    try:
        user_id, dist_id = 1, 101
        fo_id = fodb.create_filled_order(
            conn=conn, user_id=user_id, distributor_id=dist_id,
            distributor_name_raw="Balaji Homedecor", category="Bath", season="AW26",
        )
        fodb.insert_filled_order_item(
            conn, fo_id,
            {
                "item_key": "rimzim_bath_towel", "brand": "Rimzim", "size": "Bath Towel",
                "product_type": "Towel", "raw_qty_value": 360, "detected_unit": "pcs",
                "final_piece_qty": 360, "is_clean_bale_multiple": True, "matched": True,
                "mrp": 500, "ptr": 350, "ex_mill_price": 300,
            },
        )

        res = auto_match.auto_attach_so_to_filled_order(
            conn=conn, user_id=user_id, distributor_id=dist_id,
            filename="bath_so.pdf", pre_analyzed_pack=_bath_pack(),
            tracking_id=557, expected_fo_id=fo_id,
            workspace_id="ws", source="so_upload",
        )
        assert res is not None and res["status"] == "created"

        entries = matchlog.list_decisions(conn, user_id=user_id, workspace_id="ws")
        assert entries[0]["outcome"] == matchlog.OUTCOME_MATCHED_NEW
        assert entries[0]["needs_attention"] is False
        assert entries[0]["run_id"] == res["run_id"]
        assert entries[0]["filled_order_id"] == fo_id
        assert matchlog.count_needs_attention(conn, user_id=user_id, workspace_id="ws") == 0
    finally:
        conn.close()


def test_needs_attention_filter_and_user_isolation():
    conn, _ = _setup_db()
    try:
        matchlog.record(
            conn, user_id=1, workspace_id="ws", outcome=matchlog.OUTCOME_MATCHED_NEW,
            detail="ok",
        )
        matchlog.record(
            conn, user_id=1, workspace_id="ws", outcome=matchlog.OUTCOME_SKIPPED_NO_FO,
            detail="needs a human",
        )
        matchlog.record(
            conn, user_id=2, workspace_id="ws", outcome=matchlog.OUTCOME_SKIPPED_NO_FO,
            detail="another user's row",
        )

        only_attention = matchlog.list_decisions(
            conn, user_id=1, workspace_id="ws", needs_attention_only=True
        )
        assert [e["detail"] for e in only_attention] == ["needs a human"]

        assert matchlog.count_needs_attention(conn, user_id=1, workspace_id="ws") == 1
        # Another user's rows must never leak into this user's view.
        assert len(matchlog.list_decisions(conn, user_id=1, workspace_id="ws")) == 2
    finally:
        conn.close()


def test_dismiss_silences_the_reminder_without_deleting_anything():
    """The real business case: an SO with no Filled Order, and none coming
    (verbal/top-up order). Dismiss must stop the nagging, but the record —
    and therefore the SO's own tracking, CI matching and payments — must be
    completely untouched."""
    conn, _ = _setup_db()
    try:
        user_id = 1
        entry_id = matchlog.record(
            conn, user_id=user_id, workspace_id="ws",
            outcome=matchlog.OUTCOME_SKIPPED_NO_FO,
            detail="No Bath Filled Order for this distributor",
            tracking_id=901, so_numbers=["102876211"],
        )
        assert matchlog.count_needs_attention(conn, user_id=user_id, workspace_id="ws") == 1

        assert matchlog.dismiss(
            conn, entry_id=entry_id, user_id=user_id, reason="No FO expected"
        ) is True

        # Reminder gone.
        assert matchlog.count_needs_attention(conn, user_id=user_id, workspace_id="ws") == 0
        assert matchlog.list_decisions(
            conn, user_id=user_id, workspace_id="ws", needs_attention_only=True
        ) == []

        # But the row itself is intact and still readable, with the reason.
        full = matchlog.list_decisions(conn, user_id=user_id, workspace_id="ws")
        assert len(full) == 1
        assert full[0]["dismissed"] is True
        assert full[0]["dismissed_reason"] == "No FO expected"
        assert full[0]["tracking_id"] == 901
        assert "102876211" in (full[0]["so_numbers"] or "")
    finally:
        conn.close()


def test_dismiss_is_reversible_and_user_scoped():
    conn, _ = _setup_db()
    try:
        entry_id = matchlog.record(
            conn, user_id=1, workspace_id="ws",
            outcome=matchlog.OUTCOME_SKIPPED_NO_FO, detail="x",
        )
        # Another user must not be able to touch it.
        assert matchlog.dismiss(conn, entry_id=entry_id, user_id=2) is False
        assert matchlog.count_needs_attention(conn, user_id=1, workspace_id="ws") == 1

        assert matchlog.dismiss(conn, entry_id=entry_id, user_id=1) is True
        # Dismissing twice is a no-op, not an error state.
        assert matchlog.dismiss(conn, entry_id=entry_id, user_id=1) is False

        assert matchlog.restore(conn, entry_id=entry_id, user_id=1) is True
        assert matchlog.count_needs_attention(conn, user_id=1, workspace_id="ws") == 1
        assert matchlog.list_decisions(conn, user_id=1, workspace_id="ws")[0]["dismissed"] is False
    finally:
        conn.close()


def test_schema_upgrade_adds_columns_to_an_existing_table_without_data_loss():
    """The log table already shipped without these columns. CREATE TABLE IF
    NOT EXISTS does nothing to an existing table, so the upgrade must ALTER
    it in place — and must not drop or recreate anything."""
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    conn = sqlite3.connect(path)
    try:
        # Recreate the ORIGINAL shipped schema (no dismissed_* columns).
        conn.execute(
            "CREATE TABLE fo_so_auto_match_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
            "workspace_id TEXT, created_at TEXT NOT NULL, source TEXT, "
            "outcome TEXT NOT NULL, needs_attention INTEGER NOT NULL DEFAULT 0, "
            "detail TEXT, distributor_id INTEGER, tracking_id INTEGER, "
            "so_numbers TEXT, so_category TEXT, filled_order_id INTEGER, "
            "fo_category TEXT, fo_season TEXT, run_id INTEGER, archive_ids TEXT)"
        )
        conn.execute(
            "INSERT INTO fo_so_auto_match_log "
            "(user_id, workspace_id, created_at, outcome, needs_attention, detail) "
            "VALUES (1, 'ws', '2026-08-29T00:00:00+00:00', ?, 1, 'pre-existing row')",
            (matchlog.OUTCOME_SKIPPED_NO_FO,),
        )
        conn.commit()

        matchlog.ensure_schema(conn)

        columns = {row[1] for row in conn.execute("PRAGMA table_info(fo_so_auto_match_log)")}
        assert {"dismissed_at", "dismissed_reason"} <= columns
        # The row that was already there survived, and still works.
        entries = matchlog.list_decisions(conn, user_id=1, workspace_id="ws")
        assert len(entries) == 1
        assert entries[0]["detail"] == "pre-existing row"
        assert entries[0]["dismissed"] is False
        assert matchlog.count_needs_attention(conn, user_id=1, workspace_id="ws") == 1
        # ensure_schema is idempotent — running it again must not fail.
        matchlog.ensure_schema(conn)
    finally:
        conn.close()


def test_logging_failure_never_breaks_the_match():
    """record() must swallow its own errors — a broken log must not be able
    to stop a legitimate match from being saved."""
    closed = sqlite3.connect(":memory:")
    closed.close()
    assert matchlog.record(
        closed, user_id=1, outcome=matchlog.OUTCOME_MATCHED_NEW, detail="x"
    ) is None

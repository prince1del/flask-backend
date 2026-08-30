"""Balaji Mother SO + split revision (replace) flow."""

from __future__ import annotations

import sqlite3

import filled_orders_db as fodb
from app.services import fo_so_match_db as matchdb
from app.services import fo_so_revision as sorev


def _conn(tmp_path):
    path = tmp_path / "rev.sqlite3"
    conn = sqlite3.connect(str(path))
    matchdb.ensure_schema(conn)
    fodb.ensure_schema(conn)
    return conn


def _line(so: str, mat: str, qty: float, net: float) -> dict:
    return {
        "so_number": so,
        "material_code": mat,
        "product_name": mat,
        "product_detail": mat,
        "brand": mat.split()[0] if mat else "",
        "size": mat,
        "qty": qty,
        "net_amount": net,
        "gst_amount": 0,
        "total_amount": net,
    }


def test_balaji_split_so_rar_triggers_replace_not_split_dialog(tmp_path):
    """split so.rar = same SO# with reduced qty → replace_confirm (not split_or_additional)."""
    conn = _conn(tmp_path)
    uid = 1
    fo_id = fodb.create_filled_order(
        conn, uid, 1, "Balaji Homedecor", "Bed", "AW26", total_lines=1, matched_lines=1
    )

    mother_lines = [
        _line("102876193", "525B DB BS", 1044, 1_013_309.64),
        _line("102876251", "525B QB BS", 792, 933_483.6),
        _line("102876310", "525B KS BS", 360, 481_409.0),
    ]
    mother_pack = {"line_detail": mother_lines, "meta": {"source_filename": "Mother SO.zip"}}
    matchdb.save_match_run(
        conn,
        user_id=uid,
        match_payload={"fo": {"id": fo_id}, "match": {"totals": {}, "counts": {}, "rows": []}},
        so_pack=mother_pack,
        so_line_detail=mother_lines,
        so_buyer_label="Balaji Homedecor",
    )
    existing = sorev.get_latest_run_for_fo(conn, user_id=uid, filled_order_id=fo_id)

    split_lines = [
        _line("102876193", "525B DB BS", 648, 628_000.0),
        _line("102876251", "525B QB BS", 540, 636_000.0),
    ]
    split_pack = {"line_detail": split_lines, "meta": {"source_filename": "split so.rar"}}
    conflicts = matchdb.find_so_number_conflicts(conn, ["102876193", "102876251"])
    decision = sorev.analyze_incoming_against_existing(
        existing_run=existing,
        so_pack=split_pack,
        conflicts=conflicts,
    )

    assert decision["action"] == "replace_confirm"
    assert decision["action"] != "split_or_additional"
    nums = {c["so_number"] for c in decision.get("compares") or []}
    assert nums == {"102876193", "102876251"}


def test_after_replace_leftover_opens_for_additional(tmp_path):
    """After 193 1044→648, FO leftover absorbs a new child SO as Additional."""
    conn = _conn(tmp_path)
    uid = 2
    fo_id = fodb.create_filled_order(
        conn, uid, 1, "Balaji Homedecor", "Bed", "AW26", total_lines=1, matched_lines=1
    )
    fodb.insert_filled_order_item(
        conn,
        fo_id,
        {
            "item_key": "525B|DB BS",
            "brand": "525B",
            "size": "DB BS",
            "raw_qty_value": 1044,
            "detected_unit": "pieces",
            "final_piece_qty": 1044,
            "matched": True,
            "is_clean_bale_multiple": False,
        },
    )

    mother = [_line("102876193", "525B DB BS", 1044, 1_013_309.64)]
    run = matchdb.save_match_run(
        conn,
        user_id=uid,
        match_payload={
            "fo": {"id": fo_id, "distributor_id": 1},
            "match": {
                "totals": {"fo_qty": 1044, "so_qty": 1044},
                "counts": {"MATCH": 1},
                "rows": [{"brand": "525B", "size": "DB BS", "fo_qty": 1044, "so_qty": 1044, "status": "MATCH"}],
            },
        },
        so_pack={"line_detail": mother},
        so_line_detail=mother,
    )
    existing = matchdb.get_match_run(conn, int(run["id"]), user_id=uid)

    revised = [_line("102876193", "525B DB BS", 648, 628_000.0)]
    merged = sorev.merge_lines_for_replace(existing["so_line_detail"], revised, {"102876193"})
    existing["so_line_detail"] = merged
    existing["rows"] = [
        {
            "brand": "525B",
            "size": "DB BS",
            "fo_qty": 1044,
            "so_qty": 648,
            "status": "QTY_MISMATCH",
        }
    ]
    leftover = sorev.fo_qty_leftover(existing)
    assert leftover >= 396 - 1

    child = [_line("102876543", "525B DB BS", 396, 380_000.0)]
    child_pack = {"line_detail": child}
    decision = sorev.analyze_incoming_against_existing(
        existing_run=existing,
        so_pack=child_pack,
        conflicts=[],
    )
    assert decision["action"] == "save_new"
    assert decision.get("auto_additional") is True

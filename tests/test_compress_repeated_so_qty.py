"""SO qty compress must keep real multi-line packs, only collapse header stamps."""

from app.services.fo_so_match_db import compress_repeated_so_qty


def test_identical_pack_lines_sum_not_single_line():
    # Bug: 24×12 collapsed to 12 → CI 288 vs SO 12 QTY MISMATCH.
    qtys = [12.0] * 24
    assert compress_repeated_so_qty(qtys, bridge=288.0) == 288.0
    assert compress_repeated_so_qty(qtys, bridge=0.0) == 288.0
    assert compress_repeated_so_qty(qtys, bridge=12.0) == 288.0


def test_header_stamp_collapsed_to_bridge():
    # SO header total stamped on every design line.
    qtys = [648.0] * 16
    assert compress_repeated_so_qty(qtys, bridge=648.0) == 648.0


def test_mixed_real_lines_keep_sum():
    qtys = [72.0, 216.0]
    assert compress_repeated_so_qty(qtys, bridge=288.0) == 288.0
    assert compress_repeated_so_qty(qtys, bridge=12.0) == 288.0


def test_single_line():
    assert compress_repeated_so_qty([108.0], bridge=108.0) == 108.0
    assert compress_repeated_so_qty([108.0], bridge=0.0) == 108.0

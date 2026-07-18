"""Permanent invariants for HoP rate-matrix delete / clear / key sync.

These tests lock the fixes for:
- stale product_key vs live matrix key (Delete appeared to do nothing)
- empty sheet ghost columns after Clear vendor / failed OCR
- Clear all must wipe lines AND sheet headers
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app import hop_ops
from app.hop_rate_compare import product_match_key
from app.hop_schema import HOP_WORKSPACE_ID, ensure_hop_schema


def _conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "hop_delete_invariants.sqlite3"
    ensure_hop_schema(str(db))
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def test_delete_by_line_ids_works_even_with_stale_product_key(tmp_path: Path):
    conn = _conn(tmp_path)
    try:
        sheet = hop_ops.create_rate_sheet(
            conn,
            HOP_WORKSPACE_ID,
            {
                "supplier_name": "GSB",
                "lines": [
                    {"product_name": "Spa Face Towel", "size": "12x12", "rate": 39, "gst_pct": 5},
                    {"product_name": "Spa Hand Towel", "size": "16x24", "rate": 90, "gst_pct": 5},
                ],
            },
        )
        face = next(ln for ln in sheet["lines"] if "Face" in ln["product_name"])
        # Corrupt stored key the way old builds did
        conn.execute(
            "UPDATE hop_rate_lines SET product_key='face_towel' WHERE id=?",
            (face["id"],),
        )
        conn.commit()

        result = hop_ops.clear_rate_lines(
            conn,
            HOP_WORKSPACE_ID,
            product_keys=["face_towel|12x12|spa"],  # UI live key
            line_ids=[int(face["id"])],
        )
        assert result["deleted_lines"] == 1
        left = [r["product_name"] for r in conn.execute(
            "SELECT product_name FROM hop_rate_lines WHERE workspace_id=?",
            (HOP_WORKSPACE_ID,),
        )]
        assert left == ["Spa Hand Towel"]
    finally:
        conn.close()


def test_schema_ensure_heals_stale_keys(tmp_path: Path):
    db = tmp_path / "hop_heal.sqlite3"
    ensure_hop_schema(str(db))
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        hop_ops.create_rate_sheet(
            conn,
            HOP_WORKSPACE_ID,
            {
                "supplier_name": "GSB",
                "lines": [{"product_name": "Spa Face Towel", "size": "12x12", "rate": 39, "gst_pct": 5}],
            },
        )
        conn.execute(
            "UPDATE hop_rate_lines SET product_key='face_towel' WHERE workspace_id=?",
            (HOP_WORKSPACE_ID,),
        )
        conn.commit()
        conn.close()

        # Boot path must heal
        ensure_hop_schema(str(db))
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT product_key, size, product_name, quality, brand FROM hop_rate_lines WHERE workspace_id=?",
            (HOP_WORKSPACE_ID,),
        ).fetchone()
        live = product_match_key(row["product_name"], row["size"], row["quality"], row["brand"])
        assert row["product_key"] == live
        assert "spa" in row["product_key"]
        assert "12x12" in row["product_key"] or row["size"] == "12x12"
    finally:
        conn.close()


def test_clear_all_removes_sheets_and_empty_headers_never_linger(tmp_path: Path):
    conn = _conn(tmp_path)
    try:
        hop_ops.create_rate_sheet(
            conn,
            HOP_WORKSPACE_ID,
            {
                "supplier_name": "UMD",
                "lines": [{"product_name": "Bedsheet", "size": "110x112", "rate": 760, "gst_pct": 5}],
            },
        )
        # Simulate empty ghost sheet (old allow_empty path)
        conn.execute(
            """
            INSERT INTO hop_rate_sheets (
                workspace_id, supplier_name, title, source_type, status, created_at, updated_at
            ) VALUES (?, 'Bharat', 'empty', 'image', 'active', 't', 't')
            """,
            (HOP_WORKSPACE_ID,),
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) c FROM hop_rate_sheets").fetchone()["c"] == 2

        hop_ops.clear_rate_lines(conn, HOP_WORKSPACE_ID, clear_all=True)
        assert conn.execute("SELECT COUNT(*) c FROM hop_rate_lines").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM hop_rate_sheets").fetchone()["c"] == 0

        # Recreate one sheet; prune must drop empty ghosts on matrix build
        hop_ops.create_rate_sheet(
            conn,
            HOP_WORKSPACE_ID,
            {
                "supplier_name": "Bharat",
                "lines": [{"product_name": "Bath Mat", "size": "20x30", "rate": 199, "gst_pct": 5}],
            },
        )
        conn.execute(
            """
            INSERT INTO hop_rate_sheets (
                workspace_id, supplier_name, title, source_type, status, created_at, updated_at
            ) VALUES (?, 'Ghost', 'empty', 'image', 'active', 't', 't')
            """,
            (HOP_WORKSPACE_ID,),
        )
        conn.commit()
        matrix = hop_ops.rate_comparison_matrix(conn, HOP_WORKSPACE_ID)
        assert len(matrix["suppliers"]) == 1
        assert matrix["suppliers"][0]["supplier_name"] == "Bharat"
        assert conn.execute("SELECT COUNT(*) c FROM hop_rate_sheets").fetchone()["c"] == 1
    finally:
        conn.close()


def test_create_rejects_empty_sheet_without_allow_flag(tmp_path: Path):
    conn = _conn(tmp_path)
    try:
        try:
            hop_ops.create_rate_sheet(
                conn,
                HOP_WORKSPACE_ID,
                {"supplier_name": "UMD", "source_file_path": "/tmp/x.jpg", "lines": []},
            )
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "at least one valid rate line" in str(exc).lower()
        assert conn.execute("SELECT COUNT(*) c FROM hop_rate_sheets").fetchone()["c"] == 0
    finally:
        conn.close()


def test_matrix_product_key_matches_stored_after_sync(tmp_path: Path):
    conn = _conn(tmp_path)
    try:
        hop_ops.create_rate_sheet(
            conn,
            HOP_WORKSPACE_ID,
            {
                "supplier_name": "GSB",
                "lines": [
                    {"product_name": "Spa Face Towel", "size": "12x12", "rate": 39, "gst_pct": 5},
                    {
                        "product_name": "Hotel Plain Premium Face Towel",
                        "size": "12x12",
                        "rate": 35,
                        "gst_pct": 5,
                    },
                ],
            },
        )
        matrix = hop_ops.rate_comparison_matrix(conn, HOP_WORKSPACE_ID)
        assert len(matrix["products"]) == 2
        stored = {
            r["product_key"]
            for r in conn.execute(
                "SELECT product_key FROM hop_rate_lines WHERE workspace_id=?",
                (HOP_WORKSPACE_ID,),
            )
        }
        matrix_keys = {p["product_key"] for p in matrix["products"]}
        assert stored == matrix_keys
    finally:
        conn.close()

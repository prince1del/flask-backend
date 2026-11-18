"""Units / categories: "PCS" and "pcs" are one entry (made-up company)."""
from __future__ import annotations

from app import hop_settings as S
from app.hop_db import connect
from tests.test_hop_reports import co  # noqa: F401  (fixture)
from app.hop_schema import HOP_WORKSPACE_ID as WS


def test_unit_and_category_case_insensitive(co):
    with connect(co["path"]) as conn:
        a = S.create_unit(conn, WS, "Bundle")
        assert S.create_unit(conn, WS, "bundle")["id"] == a["id"]
        conn.execute("INSERT INTO hop_units (workspace_id, name, created_at) VALUES (?, 'BUNDLE', 'x')", (WS,))
        assert [u["name"].lower() for u in S.list_units(conn, WS)].count("bundle") == 1
        c = S.create_item_category(conn, WS, "Lamps")
        assert S.create_item_category(conn, WS, " LAMPS ")["id"] == c["id"]

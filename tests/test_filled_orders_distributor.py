"""Unit tests for filename → distributor suggestion."""

import importlib
import sqlite3
from pathlib import Path

import pytest

from filled_orders_distributor import normalize_filename_stem, suggest_distributor_from_filename


def test_normalize_filename_stem():
    assert normalize_filename_stem("BND.xlsx") == "bnd"
    assert normalize_filename_stem("DCA Order.xlsx") == "dca order"
    assert normalize_filename_stem("KAG_AGRA.xlsx") == "kag agra"


def test_suggest_by_nickname(tmp_path, monkeypatch):
    db_path = tmp_path / "dist.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(str(db_path))
    dist_id = db.add_master_distributor(
        "Bernina Contact",
        firm_name="Bernina International P Ltd",
        firm_nick_name="BND",
        workspace_id="ws-1",
    )

    suggestion = suggest_distributor_from_filename("BND.xlsx", "ws-1", db_path=str(db_path))
    assert suggestion is not None
    assert suggestion["id"] == dist_id
    assert "bnd" in suggestion["match_reason"].lower()


def test_suggest_by_known_alias(tmp_path, monkeypatch):
    db_path = tmp_path / "dist2.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from centralized_db_system.db import CentralizedDB

    db = CentralizedDB(str(db_path))
    dist_id = db.add_master_distributor(
        "Kalra",
        firm_name="Kalra Agencies",
        workspace_id="ws-1",
    )

    suggestion = suggest_distributor_from_filename("kag.xlsx", "ws-1", db_path=str(db_path))
    assert suggestion is not None
    assert suggestion["id"] == dist_id

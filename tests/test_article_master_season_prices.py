"""Season price snapshots + Celebrating India spelling."""
import sqlite3
from pathlib import Path

import article_master_db as amdb
import article_master_parser as amparser


def _conn(tmp_path):
    schema = Path(__file__).resolve().parent.parent / "article_master_schema.sql"
    conn = sqlite3.connect(tmp_path / "season.sqlite3")
    conn.executescript(schema.read_text(encoding="utf-8"))
    amdb.create_category(conn, 1, "Bed", ["brand", "TC", "size"], is_confirmed=True)
    return conn


def _article(brand, size, mrp, ptr, ex_mill, season, tc="100"):
    return {
        "category": "Bed",
        "brand": brand,
        "size": size,
        "product_type": "Bedsheet",
        "mrp": mrp,
        "ptr": ptr,
        "ex_mill_price": ex_mill,
        "bale_pack_size": 18,
        "season_tag": season,
        "item_key": f"{brand.upper()}|{tc}|{size}",
        "extra_attributes": {"TC": tc},
    }


def test_celebrating_india_spelling_and_binb_separate():
    assert amparser.normalize_brand_spelling("Celebareting India") == "Celebrating India"
    assert amparser.normalize_brand_spelling("Celebareting India (BINB)") == "Celebrating India (BINB)"
    assert amparser.normalize_brand_spelling("Celebrating India") == "Celebrating India"
    assert amparser.normalize_brand_spelling("Celebrating India (BINB)") == "Celebrating India (BINB)"
    assert amparser.normalize_brand_spelling("Celebrating India") != amparser.normalize_brand_spelling(
        "Celebrating India (BINB)"
    )
    # Fuzzy match must not collapse BINB into plain Celebrating India
    assert not amparser.brands_match_fuzzy("Celebrating India", "Celebrating India (BINB)")


def test_binb_alias_merge_rejected(tmp_path):
    conn = _conn(tmp_path)
    try:
        amdb.upsert_brand_alias(conn, 1, "Celebrating India (BINB)", "Celebrating India")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "BINB" in str(exc)
    conn.close()


def test_season_tag_from_filename():
    assert amparser.suggest_season_tag_from_filename("GT SS-25 Bedsheet Booking.xlsx") == "SS-25"
    assert amparser.suggest_season_tag_from_filename("BS SS-26 BOOKING SHEET.xlsx") == "SS-26"
    assert amparser.suggest_season_tag_from_filename("Order sheet AW26.xlsx") == "AW-26"
    assert amparser.normalize_season_tag("aw26") == "AW-26"
    assert amparser.normalize_season_tag("SS25") == "SS-25"


def test_three_season_upsert_and_hide_missing(tmp_path):
    conn = _conn(tmp_path)
    base = _article("Aster", "DB BS", 100, 70, 50, "SS-25")
    art, created, _ = amdb.upsert_article(conn, 1, base, source_filename="ss25.xlsx")
    assert created
    assert art["mrp"] == 100

    amdb.upsert_article(
        conn, 1,
        {**base, "mrp": 200, "ptr": 140, "ex_mill_price": 90, "season_tag": "SS-26"},
        source_filename="ss26.xlsx",
    )
    amdb.upsert_article(
        conn, 1,
        {**base, "mrp": 250, "ptr": 180, "ex_mill_price": 110, "season_tag": "AW-26"},
        source_filename="aw26.xlsx",
    )
    latest = amdb.get_article_by_item_key(conn, 1, base["item_key"])
    assert latest["mrp"] == 250
    assert latest["season_tag"] == "AW-26"

    grid = amdb.get_season_prices_last_n(conn, latest["id"], 1, limit=3)
    assert grid["seasons"] == ["SS-25", "SS-26", "AW-26"]
    assert grid["rows"]["mrp"]["SS-25"] == 100
    assert grid["rows"]["mrp"]["SS-26"] == 200
    assert grid["rows"]["mrp"]["AW-26"] == 250
    conn.close()


def test_missing_season_column_hidden(tmp_path):
    conn = _conn(tmp_path)
    base = _article("Cardinal", "SB BS", 200, 140, 90, "SS-26")
    art, _, _ = amdb.upsert_article(conn, 1, base)
    amdb.upsert_article(
        conn, 1,
        {**base, "mrp": 250, "ptr": 180, "ex_mill_price": 110, "season_tag": "AW-26"},
    )
    latest = amdb.get_article_by_item_key(conn, 1, base["item_key"])
    grid = amdb.get_season_prices_last_n(conn, latest["id"], 1, limit=3)
    assert grid["seasons"] == ["SS-26", "AW-26"]
    assert "SS-25" not in grid["seasons"]
    conn.close()


def test_older_season_does_not_clobber_latest(tmp_path):
    conn = _conn(tmp_path)
    base = _article("Flora", "KS BS", 250, 180, 110, "AW-26")
    amdb.upsert_article(conn, 1, base)
    amdb.upsert_article(
        conn, 1,
        {**base, "mrp": 100, "ptr": 70, "ex_mill_price": 50, "season_tag": "SS-25"},
        source_filename="old.xlsx",
    )
    latest = amdb.get_article_by_item_key(conn, 1, base["item_key"])
    assert latest["mrp"] == 250
    assert latest["season_tag"] == "AW-26"
    grid = amdb.get_season_prices_last_n(conn, latest["id"], 1, limit=3)
    assert grid["rows"]["mrp"]["SS-25"] == 100
    assert grid["rows"]["mrp"]["AW-26"] == 250
    conn.close()


def test_list_amounts_always_latest_season(tmp_path):
    """Even if core row is stale, get_all_articles heals to latest season prices."""
    conn = _conn(tmp_path)
    base = _article("Aster", "DB BS", 999, 700, 580, "SS-25")
    art, _, _ = amdb.upsert_article(conn, 1, base)
    # Simulate stale core + newer season snapshot (bug that showed SS-25 on list)
    amdb.upsert_season_prices(
        conn, art["id"], "AW-26",
        {"mrp": 1049, "ptr": 719.31, "ex_mill_price": 625.49},
        source_filename="aw26.xlsx",
    )
    conn.execute(
        "UPDATE article_master SET mrp = 999, ptr = 700, ex_mill_price = 580, season_tag = 'SS-25' WHERE id = ?",
        (art["id"],),
    )
    conn.commit()
    listed = amdb.get_all_articles(conn, 1)
    row = next(a for a in listed if a["id"] == art["id"])
    assert row["season_tag"] == "AW-26"
    assert row["mrp"] == 1049
    assert abs(float(row["ptr"]) - 719.31) < 0.01
    healed = amdb.get_article_by_item_key(conn, 1, base["item_key"])
    assert healed["mrp"] == 1049
    assert healed["season_tag"] == "AW-26"
    conn.close()


def test_any_upload_order_display_is_latest_season(tmp_path):
    """Whatever order seasons are uploaded, core/list amounts = chronologically latest."""
    import itertools

    seasons = [
        ("SS-25", 100, 70, 50),
        ("SS-26", 200, 140, 90),
        ("AW-26", 250, 180, 110),
    ]
    for idx, order in enumerate(itertools.permutations(seasons)):
        sub = tmp_path / f"ord{idx}"
        sub.mkdir()
        conn = _conn(sub)
        base = _article("OrderProof", "DB BS", 1, 1, 1, order[0][0], tc="77")
        first = {
            **base,
            "mrp": order[0][1],
            "ptr": order[0][2],
            "ex_mill_price": order[0][3],
            "season_tag": order[0][0],
        }
        amdb.upsert_article(conn, 1, first, source_filename=f"{order[0][0]}.xlsx")
        for tag, mrp, ptr, ex in order[1:]:
            amdb.upsert_article(
                conn, 1,
                {**base, "mrp": mrp, "ptr": ptr, "ex_mill_price": ex, "season_tag": tag},
                source_filename=f"{tag}.xlsx",
            )
        art = amdb.get_article_by_item_key(conn, 1, base["item_key"])
        assert art["season_tag"] == "AW-26", order
        assert art["mrp"] == 250, order
        assert art["ptr"] == 180, order
        assert art["ex_mill_price"] == 110, order
        listed = amdb.get_all_articles(conn, 1)
        row = next(a for a in listed if a["id"] == art["id"])
        assert row["mrp"] == 250 and row["season_tag"] == "AW-26", order
        grid = amdb.get_season_prices_last_n(conn, art["id"], 1, limit=3)
        assert grid["seasons"] == ["SS-25", "SS-26", "AW-26"], order
        conn.close()


def test_replace_older_season_does_not_clobber_latest(tmp_path):
    conn = _conn(tmp_path)
    base = _article("ReplaceProof", "SB BS", 250, 180, 110, "AW-26", tc="88")
    art, _, _ = amdb.upsert_article(conn, 1, base)
    older = {
        **base,
        "mrp": 100,
        "ptr": 70,
        "ex_mill_price": 50,
        "season_tag": "SS-25",
    }
    updated, _ = amdb.replace_article_from_upload(
        conn, 1, art["id"], older, source_filename="ss25.xlsx",
    )
    assert updated["mrp"] == 250
    assert updated["season_tag"] == "AW-26"
    grid = amdb.get_season_prices_last_n(conn, art["id"], 1, limit=3)
    assert grid["rows"]["mrp"]["SS-25"] == 100
    assert grid["rows"]["mrp"]["AW-26"] == 250
    conn.close()


def test_blank_season_upload_does_not_clobber_latest(tmp_path):
    conn = _conn(tmp_path)
    base = _article("BlankProof", "KS BS", 250, 180, 110, "AW-26", tc="99")
    amdb.upsert_article(conn, 1, base)
    untagged = {
        **base,
        "mrp": 1,
        "ptr": 1,
        "ex_mill_price": 1,
        "season_tag": None,
    }
    amdb.upsert_article(conn, 1, untagged, source_filename="mystery.xlsx")
    art = amdb.get_article_by_item_key(conn, 1, base["item_key"])
    assert art["mrp"] == 250
    assert art["season_tag"] == "AW-26"
    conn.close()

"""Tests for NEXORA Ask Q&A engine."""

import sqlite3
from pathlib import Path

import article_master_db as amdb
import filled_orders_db as fodb
import nexora_ask
import nexora_ask_learn as learn
from centralized_db_system.db import CentralizedDB


def _setup_db(tmp_path):
    db_path = tmp_path / "nexora_ask.sqlite3"
    schema_path = Path(__file__).resolve().parent.parent / "article_master_schema.sql"
    conn = sqlite3.connect(db_path)
    with open(schema_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.close()

    db = CentralizedDB(str(db_path))
    db.add_master_distributor(
        "Bernina",
        firm_name="Bernina International P Ltd",
        firm_nick_name="BND",
        gst_no="27AABCB1234A1Z5",
        address="12 MG Road, Mumbai",
        workspace_id="ws-1",
    )
    conn = sqlite3.connect(db_path)
    dist_id = conn.execute(
        "SELECT id FROM master_distributors WHERE firm_name LIKE '%Bernina%' LIMIT 1",
    ).fetchone()[0]

    amdb.create_category(conn, 1, "Bed", ["brand", "size"], is_confirmed=True, workspace_id="ws-1")
    amdb.upsert_article(conn, 1, {
        "category": "Bed",
        "product_type": "Bedsheet",
        "brand": "FLORENTINE",
        "size": "KING BS",
        "mrp": 999,
        "ptr": 450,
        "ex_mill_price": 400,
        "bale_pack_size": 12,
        "item_key": "FLORENTINE|KING BS",
        "extra_attributes": {},
    }, workspace_id="ws-1")
    amdb.upsert_article(conn, 1, {
        "category": "Bed",
        "product_type": "Bedsheet",
        "brand": "Aster",
        "size": "DB BS",
        "mrp": 1200,
        "ptr": 700,
        "ex_mill_price": 625.49,
        "bale_pack_size": 12,
        "item_key": "ASTER|DB BS",
        "extra_attributes": {},
    }, workspace_id="ws-1")
    amdb.upsert_article(conn, 1, {
        "category": "Bed",
        "product_type": "Bedsheet",
        "brand": "FLORENTINE",
        "size": "KS BS",
        "mrp": 2499,
        "ptr": 1100,
        "ex_mill_price": 950,
        "bale_pack_size": 12,
        "item_key": "FLORENTINE|KS BS",
        "extra_attributes": {},
    }, workspace_id="ws-1")
    fodb.ensure_schema(conn)
    order_id = fodb.create_filled_order(
        conn, 1, dist_id, "Bernina International P Ltd", "Bed", "AW26",
        total_lines=1, matched_lines=1,
    )
    fodb.insert_filled_order_item(conn, order_id, {
        "article_id": 1,
        "item_key": "FLORENTINE|KING BS",
        "brand": "FLORENTINE",
        "size": "KING BS",
        "product_type": "Bedsheet",
        "raw_qty_value": 10,
        "detected_unit": "bales",
        "final_piece_qty": 120,
        "bale_size_used": 12,
        "is_clean_bale_multiple": True,
        "matched": True,
        "mrp": 999,
        "ptr": 450,
        "ex_mill_price": 400,
    })
    conn.close()
    return db_path


def test_answers_product_qty_question(tmp_path):
    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    result = nexora_ask.answer_question(
        conn, 1,
        "Bernina ne Florentine King bedsheet mein kitna qty order kiya?",
        workspace_id="ws-1",
    )
    conn.close()
    assert result["intent"] == "item_qty"
    assert "120" in result["answer"]
    assert "Bernina" in result["answer"]


def test_answers_distributor_total(tmp_path):
    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    result = nexora_ask.answer_question(
        conn, 1,
        "Bernina total kitna hai?",
        workspace_id="ws-1",
    )
    conn.close()
    assert result["intent"] == "distributor_total"
    assert "120" in result["answer"]


def test_answers_article_ex_mill_price(tmp_path):
    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    result = nexora_ask.answer_question(
        conn, 1,
        "aster ka ex mill kitna hai",
        workspace_id="ws-1",
    )
    conn.close()
    assert result["intent"] == "article_ex_mill"
    assert "625.49" in result["answer"]
    assert "Aster" in result["answer"]
    assert "Choice Corner" not in result["answer"]
    assert "Santino" not in result["answer"]


def test_answers_distributor_nickname(tmp_path):
    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    for question in (
        "bnd kaun distributor hai",
        "BND nick name kis ka hai",
        "bnd kon hai",
        "bnd",
    ):
        result = nexora_ask.answer_question(
            conn, 1, question, workspace_id="ws-1", db_path=str(db_path),
        )
        assert result["intent"] == "distributor_nickname", question
        assert "Bernina" in result["answer"], question
        assert "BND" in result["answer"].upper(), question
    conn.close()


def test_answers_identity_question(tmp_path):
    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    result = nexora_ask.answer_question(
        conn, 1,
        "bernina kon hai",
        workspace_id="ws-1",
        db_path=str(db_path),
    )
    conn.close()
    assert result["intent"] == "party_identity"
    assert "Bernina" in result["answer"]
    assert "27AABCB1234A1Z5" in result["answer"]


def test_answers_article_mrp(tmp_path):
    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    result = nexora_ask.answer_question(
        conn, 1,
        "aster ki mrp kitni hai",
        workspace_id="ws-1",
    )
    conn.close()
    assert result["intent"] == "article_price"
    assert "1200" in result["answer"] or "1,200" in result["answer"]


def test_learned_phrase_rewrites_question(tmp_path):
    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    learn.teach_phrase(
        conn,
        workspace_id="ws-1",
        user_phrase="bernna gst kya hai",
        canonical_question="bernina ka gst number aur address",
        created_by=1,
    )
    result = nexora_ask.answer_question(
        conn, 1,
        "bernna gst kya hai",
        workspace_id="ws-1",
        db_path=str(db_path),
    )
    conn.close()
    assert result["intent"] == "party_profile"
    assert result["data"].get("learned")
    assert "27AABCB1234A1Z5" in result["answer"]


def test_answers_florentine_ks_bs_mrp(tmp_path):
    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    for question in ("florentine ksbs ki mrp", "florentine ks bs ki mrp"):
        result = nexora_ask.answer_question(conn, 1, question, workspace_id="ws-1")
        assert result["intent"] == "article_price", question
        assert "2499" in result["answer"] or "2,499" in result["answer"], question
        assert "FLORENTINE" in result["answer"].upper() or "Florentine" in result["answer"], question
    conn.close()


def test_answers_distributor_party_profile(tmp_path):
    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    result = nexora_ask.answer_question(
        conn, 1,
        "bernna ka gst number aur address",
        workspace_id="ws-1",
        db_path=str(db_path),
    )
    conn.close()
    assert result["intent"] == "party_profile"
    assert "27AABCB1234A1Z5" in result["answer"]
    assert "MG Road" in result["answer"]
    # Only asked fields — not the whole master dump
    assert "Nick" not in result["answer"]
    assert "Distributor code" not in result["answer"]
    assert "Phone" not in result["answer"]


def test_answers_gst_only_not_full_profile(tmp_path):
    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    result = nexora_ask.answer_question(
        conn, 1,
        "Bernina ka GST number?",
        workspace_id="ws-1",
        db_path=str(db_path),
    )
    conn.close()
    assert result["intent"] == "party_profile"
    assert "27AABCB1234A1Z5" in result["answer"]
    assert "GST" in result["answer"]
    assert "MG Road" not in result["answer"]
    assert "Nick" not in result["answer"]
"""Ask NEXORA workspace isolation — hop vs BD data never mixes."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import hop_ask
import nexora_ask
from app.hop_db import connect, create_customer, create_project
from app.hop_schema import HOP_WORKSPACE_ID, ensure_hop_schema


class AskNexoraIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "ask_iso.sqlite3")
        ensure_hop_schema(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hop_workspace_answers_from_hop_only(self):
        with connect(self.db) as conn:
            c = create_customer(conn, HOP_WORKSPACE_ID, {"company": "Prizm Hotels"})
            create_project(
                conn,
                HOP_WORKSPACE_ID,
                {
                    "project_name": "Holiday Inn Dwarka",
                    "customer_id": c["id"],
                    "probability_pct": 85,
                    "project_value": 500000,
                    "hotel_name": "Holiday Inn",
                },
            )
            result = nexora_ask.answer_question(
                conn,
                user_id=99,
                question="Which hotel projects have the highest probability?",
                workspace_id=HOP_WORKSPACE_ID,
                db_path=self.db,
            )
        self.assertEqual(result["intent"], "high_probability_projects")
        self.assertIn("Holiday Inn", result["answer"])
        self.assertEqual(result["data"].get("workspace_id"), HOP_WORKSPACE_ID)

    def test_hop_ask_rejects_other_workspace(self):
        with connect(self.db) as conn:
            with self.assertRaises(PermissionError):
                hop_ask.answer_question(
                    conn,
                    user_id=1,
                    question="help",
                    workspace_id="bombay_dyeing_gt_north",
                    db_path=self.db,
                )

    def test_bd_workspace_does_not_load_hop_projects(self):
        """Bombay Dyeing Ask path must not answer Prizm project questions from hop tables."""
        with connect(self.db) as conn:
            create_customer(conn, HOP_WORKSPACE_ID, {"company": "Secret Prizm Client"})
            # Empty FO schema for BD user
            import filled_orders_db as fodb

            fodb.ensure_schema(conn)
            result = nexora_ask.answer_question(
                conn,
                user_id=1,
                question="Which hotel projects have the highest probability?",
                workspace_id="bombay_dyeing_gt_north",
                db_path=self.db,
            )
        # Should NOT be hop intent / should not leak Prizm customer name
        self.assertNotEqual(result.get("intent"), "high_probability_projects")
        self.assertNotIn("Secret Prizm Client", result.get("answer") or "")

    def test_masters_empty_without_workspace(self):
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(nexora_ask._load_distributors(conn, None), [])
            self.assertEqual(nexora_ask._load_retailers(conn, None), [])
        finally:
            conn.close()

    def test_learned_phrases_are_per_user(self):
        import nexora_ask_learn as learn

        with connect(self.db) as conn:
            learn.teach_phrase(
                conn,
                workspace_id="ws-a",
                user_phrase="my secret alias",
                canonical_question="bernina ka gst number aur address",
                created_by=10,
            )
            hit_own, _ = learn.find_canonical_question(
                conn, "my secret alias", "ws-a", owner_user_id=10
            )
            hit_other, _ = learn.find_canonical_question(
                conn, "my secret alias", "ws-a", owner_user_id=99
            )
            self.assertIsNotNone(hit_own)
            self.assertIsNone(hit_other)

            # Prizm never gets BD seed phrases
            learn.seed_default_phrases(conn, HOP_WORKSPACE_ID)
            seeds = conn.execute(
                "SELECT COUNT(*) FROM nexora_learned_phrases WHERE workspace_id=? AND source='seed'",
                (HOP_WORKSPACE_ID,),
            ).fetchone()[0]
            self.assertEqual(seeds, 0)


if __name__ == "__main__":
    unittest.main()

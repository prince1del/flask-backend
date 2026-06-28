from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from rapidfuzz import fuzz


class ArticleMasterService:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _normalize_category(self, category: str | None) -> str:
        if not category:
            return "Uncategorized"
        cleaned = " ".join(str(category).strip().split())
        return cleaned.title()

    def _infer_category(
        self, category: str | None, existing_categories: list[str]
    ) -> str:
        normalized = self._normalize_category(category)
        if not normalized or normalized == "Uncategorized":
            return "Uncategorized"
        if normalized in existing_categories:
            return normalized

        best_match = None
        best_score = 0
        for existing in existing_categories:
            score = fuzz.ratio(normalized.lower(), existing.lower())
            if score > best_score and score >= 70:
                best_score = score
                best_match = existing
        return best_match or normalized

    def sanitize_article_payload(
        self, payload: dict[str, Any], existing_categories: list[str] | None = None
    ) -> dict[str, Any]:
        existing_categories = existing_categories or []
        category = self._infer_category(
            payload.get("category_name"), existing_categories
        )
        design_name = str(
            payload.get("design_name") or payload.get("design_code") or ""
        ).strip()
        color_way = str(payload.get("color_way") or "").strip()
        base_rate = float(payload.get("base_rate") or 0.0)
        gst_percentage = float(payload.get("gst_percentage") or 0.0)
        pcs_per_bale = float(payload.get("pcs_per_bale") or 0.0)
        status = str(payload.get("status") or "active").strip().lower() or "active"

        return {
            "category_name": category,
            "design_name": design_name.title() if design_name else "",
            "color_way": color_way.upper() if color_way else "",
            "base_rate": base_rate,
            "gst_percentage": gst_percentage,
            "pcs_per_bale": pcs_per_bale,
            "status": status,
        }

    def save_article(
        self, payload: dict[str, Any], conn: sqlite3.Connection | None = None
    ) -> int:
        connection = conn or sqlite3.connect(self.db_path)
        should_close = conn is None
        try:
            existing_categories = [
                row[0]
                for row in connection.execute(
                    "SELECT category_name FROM article_master WHERE status != 'inactive'"
                ).fetchall()
            ]
            sanitized = self.sanitize_article_payload(payload, existing_categories)
            article_id = (
                f"{sanitized['category_name'][:3].upper()}-{uuid4().hex[:10].upper()}"
            )
            created_at = datetime.now(timezone.utc).isoformat()
            cursor = connection.execute(
                """
                INSERT INTO article_master (
                    article_id, category_name, design_name, color_way, base_rate, gst_percentage, pcs_per_bale, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article_id,
                    sanitized["category_name"],
                    sanitized["design_name"],
                    sanitized["color_way"],
                    sanitized["base_rate"],
                    sanitized["gst_percentage"],
                    sanitized["pcs_per_bale"],
                    sanitized["status"],
                    created_at,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)
        finally:
            if should_close:
                connection.close()

    def list_articles_by_category(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT article_id, category_name, design_name, color_way, base_rate, gst_percentage, pcs_per_bale, status FROM article_master ORDER BY category_name, design_name, color_way"
            ).fetchall()

        return [
            {
                "article_id": row[0],
                "category_name": row[1],
                "design_name": row[2],
                "color_way": row[3],
                "base_rate": row[4],
                "gst_percentage": row[5],
                "pcs_per_bale": row[6],
                "status": row[7],
            }
            for row in rows
        ]

"""
Distributor Filled-Order Matching — DB access layer (per-user isolation)

Same user_id-scoped ownership discipline as article_master_db.py.
"""

import os
import sqlite3
from datetime import datetime

import article_master_db as amdb

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "filled_orders_schema.sql")
_schema_sql_cache = None
_schema_ensured = False

FILLED_ORDER_COLUMNS = [
    "id", "user_id", "distributor_id", "distributor_name_raw", "category", "season",
    "source_filename", "quantity_column_used", "quantity_unit_used",
    "total_lines", "matched_lines", "unmatched_lines", "flagged_lines", "created_at",
]

FILLED_ORDER_ITEM_COLUMNS = [
    "id", "filled_order_id", "article_id", "item_key", "brand", "size", "product_type",
    "raw_qty_value", "detected_unit", "final_piece_qty", "bale_size_used",
    "is_clean_bale_multiple", "matched", "mrp", "ptr", "ex_mill_price", "created_at",
]


def ensure_schema(conn):
    """Idempotent (every statement is CREATE ... IF NOT EXISTS) — safe to call
    on every request so the feature works without a separate migration step."""
    global _schema_sql_cache, _schema_ensured
    if _schema_ensured:
        try:
            conn.execute("SELECT 1 FROM filled_orders LIMIT 1")
            return
        except sqlite3.OperationalError:
            _schema_ensured = False
    if _schema_sql_cache is None:
        with open(_SCHEMA_PATH, encoding="utf-8") as f:
            _schema_sql_cache = f.read()
    conn.executescript(_schema_sql_cache)
    _ensure_filled_orders_unique_slot(conn)
    _schema_ensured = True


def _normalize_slot_value(value):
    return (value or "").strip()


def _ensure_filled_orders_unique_slot(conn):
    """One filled order per user + distributor + category + season. Dedupe legacy rows."""
    index_sql = """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_filled_orders_unique_slot
        ON filled_orders(user_id, distributor_id, category, season)
        WHERE distributor_id IS NOT NULL
    """
    try:
        conn.execute(index_sql)
        conn.commit()
        return
    except sqlite3.IntegrityError:
        pass

    dup_groups = conn.execute(
        """SELECT user_id, distributor_id, category, season, GROUP_CONCAT(id) AS ids
           FROM filled_orders
           WHERE distributor_id IS NOT NULL
           GROUP BY user_id, distributor_id, category, season
           HAVING COUNT(*) > 1"""
    ).fetchall()
    for _user_id, _dist_id, _cat, _season, ids_csv in dup_groups:
        ids = sorted(int(x) for x in (ids_csv or "").split(",") if x)
        for order_id in ids[:-1]:
            conn.execute("DELETE FROM filled_order_items WHERE filled_order_id = ?", (order_id,))
            conn.execute("DELETE FROM filled_orders WHERE id = ?", (order_id,))
    conn.commit()
    conn.execute(index_sql)
    conn.commit()


def _row_to_order_dict(row):
    return dict(zip(FILLED_ORDER_COLUMNS, row))


def _row_to_item_dict(row):
    d = dict(zip(FILLED_ORDER_ITEM_COLUMNS, row))
    d["is_clean_bale_multiple"] = bool(d["is_clean_bale_multiple"])
    d["matched"] = bool(d["matched"])
    return d


def create_filled_order(
    conn, user_id, distributor_id, distributor_name_raw, category, season,
    source_filename=None, quantity_column_used=None, quantity_unit_used=None,
    total_lines=0, matched_lines=0, unmatched_lines=0, flagged_lines=0,
):
    category = _normalize_slot_value(category)
    season = _normalize_slot_value(season)
    cursor = conn.execute(
        """INSERT INTO filled_orders
           (user_id, distributor_id, distributor_name_raw, category, season, source_filename,
            quantity_column_used, quantity_unit_used, total_lines, matched_lines,
            unmatched_lines, flagged_lines)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id, distributor_id, distributor_name_raw, category, season, source_filename,
            quantity_column_used, quantity_unit_used, total_lines, matched_lines,
            unmatched_lines, flagged_lines,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def insert_filled_order_item(conn, filled_order_id, item):
    conn.execute(
        """INSERT INTO filled_order_items
           (filled_order_id, article_id, item_key, brand, size, product_type, raw_qty_value,
            detected_unit, final_piece_qty, bale_size_used, is_clean_bale_multiple, matched,
            mrp, ptr, ex_mill_price)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            filled_order_id,
            item.get("article_id"),
            item["item_key"],
            item.get("brand"),
            item.get("size"),
            item.get("product_type"),
            item["raw_qty_value"],
            item["detected_unit"],
            item["final_piece_qty"],
            item.get("bale_size_used"),
            1 if item["is_clean_bale_multiple"] else 0,
            1 if item["matched"] else 0,
            item.get("mrp"),
            item.get("ptr"),
            item.get("ex_mill_price"),
        ),
    )
    conn.commit()


def merge_items_into_filled_order(conn, user_id, filled_order_id, items, extra_filename=None):
    """Add new lines into an existing FO, clubbing qty on the same item_key."""
    existing = conn.execute(
        """SELECT id, item_key, raw_qty_value, final_piece_qty, ex_mill_price, matched
           FROM filled_order_items WHERE filled_order_id = ?""",
        (filled_order_id,),
    ).fetchall()
    by_key = {row[1]: row for row in existing if row[1]}
    for item in items:
        key = item.get("item_key")
        if key and key in by_key:
            row_id, _, raw_qty, final_qty, ex_mill, matched = by_key[key]
            new_raw = (raw_qty or 0) + (item.get("raw_qty_value") or 0)
            new_final = (final_qty or 0) + (item.get("final_piece_qty") or 0)
            new_ex = item.get("ex_mill_price") or ex_mill
            new_matched = 1 if (matched or item.get("matched")) else 0
            conn.execute(
                """UPDATE filled_order_items
                   SET raw_qty_value = ?, final_piece_qty = ?, ex_mill_price = ?, matched = ?
                   WHERE id = ?""",
                (new_raw, new_final, new_ex, new_matched, row_id),
            )
        else:
            insert_filled_order_item(conn, filled_order_id, item)
    if extra_filename:
        row = conn.execute(
            "SELECT source_filename FROM filled_orders WHERE id = ? AND user_id = ?",
            (filled_order_id, user_id),
        ).fetchone()
        previous = (row[0] or "") if row else ""
        if extra_filename not in previous:
            combined = f"{previous} + {extra_filename}".strip(" +") if previous else extra_filename
            conn.execute(
                "UPDATE filled_orders SET source_filename = ? WHERE id = ? AND user_id = ?",
                (combined, filled_order_id, user_id),
            )
    recompute_order_counts(conn, filled_order_id)
    conn.commit()
    return get_filled_order(conn, user_id, filled_order_id)


def recompute_filled_order_quantities(conn, filled_order_id: int) -> int:
    """Re-apply qty-column rules to saved lines (fixes legacy bale miscounts)."""
    import filled_orders_parser as foparser

    row = conn.execute(
        "SELECT quantity_column_used, category FROM filled_orders WHERE id = ?",
        (filled_order_id,),
    ).fetchone()
    if not row:
        return 0
    qty_col, category = row[0], row[1]
    items = conn.execute(
        "SELECT id, raw_qty_value, bale_size_used FROM filled_order_items WHERE filled_order_id = ?",
        (filled_order_id,),
    ).fetchall()
    if not items:
        return 0
    updated = 0
    units: set[str] = set()
    for item_id, raw_qty, bale_size in items:
        unit, final_qty = foparser.normalize_quantity(
            float(raw_qty or 0),
            bale_size,
            qty_column_label=qty_col,
            category=category,
        )
        units.add(unit)
        clean = foparser.is_clean_bale_multiple(final_qty, bale_size)
        conn.execute(
            """
            UPDATE filled_order_items
            SET detected_unit = ?, final_piece_qty = ?, is_clean_bale_multiple = ?
            WHERE id = ?
            """,
            (unit, final_qty, 1 if clean else 0, item_id),
        )
        updated += 1
    if updated:
        quantity_unit_used = (
            "mixed" if len(units) > 1 else (next(iter(units)) if units else "pieces")
        )
        conn.execute(
            "UPDATE filled_orders SET quantity_unit_used = ? WHERE id = ?",
            (quantity_unit_used, filled_order_id),
        )
        recompute_order_counts(conn, filled_order_id)
        conn.commit()
    return updated


def summarize_filled_order_totals(conn, filled_order_id: int) -> dict[str, float]:
    """Roll up bales, pieces, and ex-mill value for one saved filled order."""
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(COALESCE(final_piece_qty, 0)), 0) AS total_piece_qty,
            COALESCE(SUM(COALESCE(final_piece_qty, 0) * COALESCE(ex_mill_price, 0)), 0)
                AS total_ex_mill_value,
            COALESCE(SUM(
                CASE
                    WHEN LOWER(COALESCE(detected_unit, '')) = 'bales'
                        THEN COALESCE(raw_qty_value, 0)
                    WHEN COALESCE(bale_size_used, 0) > 0
                         AND ABS(
                             (COALESCE(final_piece_qty, 0) / bale_size_used)
                             - ROUND(COALESCE(final_piece_qty, 0) / bale_size_used)
                         ) < 1e-6
                        THEN COALESCE(final_piece_qty, 0) / bale_size_used
                    ELSE 0
                END
            ), 0) AS total_bales
        FROM filled_order_items
        WHERE filled_order_id = ?
        """,
        (filled_order_id,),
    ).fetchone()
    if not row:
        return {"total_bales": 0.0, "total_piece_qty": 0.0, "total_ex_mill_value": 0.0}
    return {
        "total_bales": round(float(row[2] or 0), 2),
        "total_piece_qty": round(float(row[0] or 0), 2),
        "total_ex_mill_value": round(float(row[1] or 0), 2),
    }


def _resolve_distributor_display_name(conn, distributor_id, distributor_name_raw) -> str:
    raw = (distributor_name_raw or "").strip()
    if raw:
        return raw
    if distributor_id:
        row = conn.execute(
            "SELECT firm_name, name FROM master_distributors WHERE id = ?",
            (distributor_id,),
        ).fetchone()
        if row:
            firm, name = row
            return (firm or name or f"Distributor #{distributor_id}").strip()
    return "Unknown distributor"


def build_season_overview(conn, user_id: int) -> list[dict]:
    """One card per season; categories (Bed, Bath, … any) nest inside.

    Expand UI shows each category block with distributor totals under it.
    """
    from collections import defaultdict

    orders = list_filled_orders(conn, user_id)
    # (season, category) -> distributor buckets
    slot_groups: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    season_cats: dict[str, set[str]] = defaultdict(set)

    for order in orders:
        season = (order.get("season") or "—").strip() or "—"
        category = (order.get("category") or "—").strip() or "—"
        season_cats[season].add(category)
        dist_id = order.get("distributor_id")
        key = str(dist_id) if dist_id is not None else (order.get("distributor_name_raw") or f"order-{order['id']}")
        name = _resolve_distributor_display_name(conn, dist_id, order.get("distributor_name_raw"))
        bucket = slot_groups[(season, category)].get(key)
        if not bucket:
            bucket = {
                "distributor_name": name,
                "total_piece_qty": 0.0,
                "total_ex_mill_value": 0.0,
            }
            slot_groups[(season, category)][key] = bucket
        bucket["total_piece_qty"] += float(order.get("total_piece_qty") or 0)
        bucket["total_ex_mill_value"] += float(order.get("total_ex_mill_value") or 0)

    overview = []
    for season in sorted(season_cats.keys(), reverse=True):
        cats = sorted(season_cats[season], key=lambda c: c.lower())
        category_blocks = []
        # Season-level distributor rollup (all categories combined)
        season_dist: dict[str, dict] = {}
        for category in cats:
            rows = list(slot_groups[(season, category)].values())
            rows.sort(key=lambda r: (r["distributor_name"] or "").lower())
            for row in rows:
                row["total_piece_qty"] = round(row["total_piece_qty"], 2)
                row["total_ex_mill_value"] = round(row["total_ex_mill_value"], 2)
                dname = row["distributor_name"] or ""
                agg = season_dist.get(dname)
                if not agg:
                    agg = {
                        "distributor_name": row["distributor_name"],
                        "total_piece_qty": 0.0,
                        "total_ex_mill_value": 0.0,
                    }
                    season_dist[dname] = agg
                agg["total_piece_qty"] += row["total_piece_qty"]
                agg["total_ex_mill_value"] += row["total_ex_mill_value"]
            category_blocks.append({
                "category": category,
                "rows": rows,
                "total_piece_qty": round(sum(r["total_piece_qty"] for r in rows), 2),
                "total_ex_mill_value": round(sum(r["total_ex_mill_value"] for r in rows), 2),
            })
        season_rows = list(season_dist.values())
        season_rows.sort(key=lambda r: (r["distributor_name"] or "").lower())
        for row in season_rows:
            row["total_piece_qty"] = round(row["total_piece_qty"], 2)
            row["total_ex_mill_value"] = round(row["total_ex_mill_value"], 2)
        overview.append({
            "season": season,
            "label": season,
            "categories": category_blocks,
            "rows": season_rows,
            "total_piece_qty": round(sum(r["total_piece_qty"] for r in season_rows), 2),
            "total_ex_mill_value": round(sum(r["total_ex_mill_value"] for r in season_rows), 2),
        })
    return overview


def _enrich_filled_order_dict(conn, order: dict) -> dict:
    order.update(summarize_filled_order_totals(conn, int(order["id"])))
    return order


def get_filled_order(conn, user_id, filled_order_id):
    cols = ", ".join(FILLED_ORDER_COLUMNS)
    row = conn.execute(
        f"SELECT {cols} FROM filled_orders WHERE id = ? AND user_id = ?",
        (filled_order_id, user_id),
    ).fetchone()
    if not row:
        return None
    return _enrich_filled_order_dict(conn, _row_to_order_dict(row))


def get_filled_order_items(conn, filled_order_id):
    recompute_filled_order_quantities(conn, filled_order_id)
    cols = ", ".join(FILLED_ORDER_ITEM_COLUMNS)
    rows = conn.execute(
        f"SELECT {cols} FROM filled_order_items WHERE filled_order_id = ? ORDER BY id",
        (filled_order_id,),
    ).fetchall()
    return [_row_to_item_dict(r) for r in rows]


def list_filled_orders(conn, user_id, distributor_id=None, category=None, season=None):
    cols = ", ".join(FILLED_ORDER_COLUMNS)
    query = f"SELECT {cols} FROM filled_orders WHERE (user_id = ? OR user_id IS NULL)"
    params = [user_id]
    if distributor_id:
        query += " AND distributor_id = ?"
        params.append(distributor_id)
    if category:
        query += " AND category = ?"
        params.append(category)
    if season:
        query += " AND season = ?"
        params.append(season)
    query += " ORDER BY id DESC"
    rows = conn.execute(query, params).fetchall()
    for row in rows:
        recompute_filled_order_quantities(conn, int(row[0]))
    rows = conn.execute(query, params).fetchall()
    return [_enrich_filled_order_dict(conn, _row_to_order_dict(r)) for r in rows]


def find_filled_order_by_distributor_category_season(
    conn, user_id, distributor_id, category, season,
):
    """Return the latest saved order for this distributor + category + season, if any."""
    category = _normalize_slot_value(category)
    season = _normalize_slot_value(season)
    if not distributor_id or not category or not season:
        return None
    orders = list_filled_orders(
        conn, user_id, distributor_id=distributor_id, category=category, season=season,
    )
    return orders[0] if orders else None


def get_last_season(conn, user_id):
    row = conn.execute(
        "SELECT season FROM filled_orders WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    return row[0] if row else None


def list_seasons_matching_prefix(conn, user_id: int, prefix: str) -> list[str]:
    """Distinct season strings for this user whose value starts with prefix
    (case-insensitive) — the stored season is sometimes a bare code
    ("AW26") and sometimes a fuller label ("AW26 Bedsheet"), so Ask Nexora
    matches on the season-code prefix rather than an exact string."""
    rows = conn.execute(
        "SELECT DISTINCT season FROM filled_orders WHERE user_id = ? "
        "AND UPPER(season) LIKE ? ORDER BY season",
        (user_id, f"{prefix.upper()}%"),
    ).fetchall()
    return [r[0] for r in rows]


def list_distinct_categories(conn, user_id: int, seasons: list[str] | None = None) -> list[str]:
    if seasons:
        placeholders = ",".join("?" for _ in seasons)
        rows = conn.execute(
            f"SELECT DISTINCT category FROM filled_orders WHERE user_id = ? "
            f"AND season IN ({placeholders})",
            (user_id, *seasons),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT category FROM filled_orders WHERE user_id = ?", (user_id,),
        ).fetchall()
    return [r[0] for r in rows if r[0]]


def list_distinct_brands(conn, user_id: int, seasons: list[str] | None = None) -> list[str]:
    if seasons:
        placeholders = ",".join("?" for _ in seasons)
        rows = conn.execute(
            f"SELECT DISTINCT foi.brand FROM filled_order_items foi "
            f"JOIN filled_orders fo ON fo.id = foi.filled_order_id "
            f"WHERE fo.user_id = ? AND fo.season IN ({placeholders}) AND foi.brand IS NOT NULL",
            (user_id, *seasons),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT foi.brand FROM filled_order_items foi "
            "JOIN filled_orders fo ON fo.id = foi.filled_order_id "
            "WHERE fo.user_id = ? AND foi.brand IS NOT NULL",
            (user_id,),
        ).fetchall()
    return [r[0] for r in rows if r[0]]


def list_distinct_sizes(conn, user_id: int, seasons: list[str] | None = None) -> list[str]:
    if seasons:
        placeholders = ",".join("?" for _ in seasons)
        rows = conn.execute(
            f"SELECT DISTINCT foi.size FROM filled_order_items foi "
            f"JOIN filled_orders fo ON fo.id = foi.filled_order_id "
            f"WHERE fo.user_id = ? AND fo.season IN ({placeholders}) AND foi.size IS NOT NULL",
            (user_id, *seasons),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT foi.size FROM filled_order_items foi "
            "JOIN filled_orders fo ON fo.id = foi.filled_order_id "
            "WHERE fo.user_id = ? AND foi.size IS NOT NULL",
            (user_id,),
        ).fetchall()
    return [r[0] for r in rows if r[0]]


def query_order_value(
    conn,
    user_id: int,
    seasons: list[str],
    category: str | None = None,
    distributor_id: int | None = None,
    brand: str | None = None,
    size: str | None = None,
) -> dict:
    """Sum piece qty + ex-mill value across filled_order_items for this
    user, scoped to the given season strings and any of category/
    distributor/brand/size the caller was able to resolve from the
    question. Powers Ask Nexora's season order-value answers — same
    underlying data as the "Total value of SO" home-screen card
    (build_season_overview), just filtered down to a single figure."""
    if not seasons:
        return {"total_piece_qty": 0.0, "total_ex_mill_value": 0.0, "matched_orders": 0}
    placeholders = ",".join("?" for _ in seasons)
    # build_season_overview() reaches its numbers via list_filled_orders(),
    # which re-applies qty-column rules to every order before summing —
    # fixes stale bale-count rows left over from before a rule change.
    # Matching that here so this answer can't drift from the dashboard
    # figure it's meant to mirror.
    order_id_sql = f"SELECT id FROM filled_orders WHERE user_id = ? AND season IN ({placeholders})"
    order_id_params: list = [user_id, *seasons]
    if category:
        order_id_sql += " AND category = ? COLLATE NOCASE"
        order_id_params.append(category)
    if distributor_id:
        order_id_sql += " AND distributor_id = ?"
        order_id_params.append(distributor_id)
    for (order_id,) in conn.execute(order_id_sql, order_id_params).fetchall():
        recompute_filled_order_quantities(conn, order_id)

    sql = (
        "SELECT COALESCE(SUM(foi.final_piece_qty), 0), "
        "COALESCE(SUM(foi.final_piece_qty * COALESCE(foi.ex_mill_price, 0)), 0), "
        "COUNT(DISTINCT fo.id) "
        "FROM filled_order_items foi "
        "JOIN filled_orders fo ON fo.id = foi.filled_order_id "
        f"WHERE fo.user_id = ? AND fo.season IN ({placeholders})"
    )
    params: list = [user_id, *seasons]
    if category:
        sql += " AND fo.category = ? COLLATE NOCASE"
        params.append(category)
    if distributor_id:
        sql += " AND fo.distributor_id = ?"
        params.append(distributor_id)
    if brand:
        sql += " AND foi.brand = ? COLLATE NOCASE"
        params.append(brand)
    if size:
        sql += " AND foi.size = ? COLLATE NOCASE"
        params.append(size)
    row = conn.execute(sql, params).fetchone()
    qty, value, matched_orders = row if row else (0, 0, 0)
    return {
        "total_piece_qty": round(float(qty or 0), 2),
        "total_ex_mill_value": round(float(value or 0), 2),
        "matched_orders": int(matched_orders or 0),
    }


def get_qty_column_pref(conn, user_id, distributor_id, category):
    row = conn.execute(
        """SELECT confirmed_column_name FROM distributor_qty_column_prefs
           WHERE user_id = ? AND distributor_id = ? AND category = ?""",
        (user_id, distributor_id, category),
    ).fetchone()
    return row[0] if row else None


def save_qty_column_pref(conn, user_id, distributor_id, category, confirmed_column_name):
    conn.execute(
        """INSERT INTO distributor_qty_column_prefs
           (user_id, distributor_id, category, confirmed_column_name)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(user_id, distributor_id, category)
           DO UPDATE SET confirmed_column_name = excluded.confirmed_column_name""",
        (user_id, distributor_id, category, confirmed_column_name),
    )
    conn.commit()


def recompute_order_counts(conn, filled_order_id):
    total = conn.execute(
        "SELECT COUNT(*) FROM filled_order_items WHERE filled_order_id = ?", (filled_order_id,),
    ).fetchone()[0]
    matched = conn.execute(
        "SELECT COUNT(*) FROM filled_order_items WHERE filled_order_id = ? AND matched = 1",
        (filled_order_id,),
    ).fetchone()[0]
    flagged = conn.execute(
        "SELECT COUNT(*) FROM filled_order_items WHERE filled_order_id = ? AND is_clean_bale_multiple = 0",
        (filled_order_id,),
    ).fetchone()[0]
    conn.execute(
        """UPDATE filled_orders
           SET total_lines = ?, matched_lines = ?, unmatched_lines = ?, flagged_lines = ?
           WHERE id = ?""",
        (total, matched, total - matched, flagged, filled_order_id),
    )
    conn.commit()


def delete_filled_order(conn, user_id, filled_order_id):
    row = conn.execute(
        "SELECT id FROM filled_orders WHERE id = ? AND user_id = ?",
        (filled_order_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError("Filled order not found")
    conn.execute("DELETE FROM filled_order_items WHERE filled_order_id = ?", (filled_order_id,))
    conn.execute("DELETE FROM filled_orders WHERE id = ? AND user_id = ?", (filled_order_id, user_id))
    conn.commit()
    return True


def delete_filled_orders_by_ids(conn, user_id, raw_ids):
    """Delete many FO headers; skip missing / invalid ids. Returns count deleted."""
    deleted = 0
    for raw in raw_ids or []:
        try:
            oid = int(raw)
        except (TypeError, ValueError):
            continue
        try:
            delete_filled_order(conn, user_id, oid)
            deleted += 1
        except ValueError:
            continue
    return deleted


def delete_filled_order_item(conn, user_id, filled_order_id, item_id):
    order_row = conn.execute(
        "SELECT id FROM filled_orders WHERE id = ? AND user_id = ?",
        (filled_order_id, user_id),
    ).fetchone()
    if order_row is None:
        raise ValueError("Filled order not found")
    item_row = conn.execute(
        "SELECT id FROM filled_order_items WHERE id = ? AND filled_order_id = ?",
        (item_id, filled_order_id),
    ).fetchone()
    if item_row is None:
        raise ValueError("Item not found")
    conn.execute(
        "DELETE FROM filled_order_items WHERE id = ? AND filled_order_id = ?",
        (item_id, filled_order_id),
    )
    conn.commit()
    recompute_order_counts(conn, filled_order_id)
    return True


def _coerce_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def update_filled_order_item(conn, user_id, filled_order_id, item_id, updates):
    """Manual correction of a filled-order line; recomputes qty flags when needed."""
    import filled_orders_parser as foparser

    order_row = conn.execute(
        "SELECT id, quantity_column_used, category FROM filled_orders WHERE id = ? AND user_id = ?",
        (filled_order_id, user_id),
    ).fetchone()
    if order_row is None:
        raise ValueError("Filled order not found")

    cols = ", ".join(FILLED_ORDER_ITEM_COLUMNS)
    item_row = conn.execute(
        f"SELECT {cols} FROM filled_order_items WHERE id = ? AND filled_order_id = ?",
        (item_id, filled_order_id),
    ).fetchone()
    if item_row is None:
        raise ValueError("Item not found")

    current = _row_to_item_dict(item_row)
    allowed = {
        "brand",
        "size",
        "product_type",
        "mrp",
        "ptr",
        "ex_mill_price",
        "bale_size_used",
        "raw_qty_value",
        "detected_unit",
        "final_piece_qty",
        "matched",
    }
    merged = dict(current)
    for key, value in (updates or {}).items():
        if key not in allowed:
            continue
        if key in {"mrp", "ptr", "ex_mill_price", "bale_size_used", "raw_qty_value", "final_piece_qty"}:
            merged[key] = _coerce_float(value)
        elif key == "matched":
            merged[key] = bool(value) if not isinstance(value, str) else value.lower() in ("1", "true", "yes")
        elif key == "detected_unit":
            unit = str(value or "").strip().lower()
            merged[key] = unit if unit in ("bales", "pieces") else merged.get("detected_unit") or "pieces"
        else:
            merged[key] = (str(value).strip() if value is not None else None) or None

    recompute_qty = False
    raw_changed = (
        "raw_qty_value" in updates
        and _coerce_float(updates.get("raw_qty_value")) != _coerce_float(current.get("raw_qty_value"))
    )
    bale_changed = (
        "bale_size_used" in updates
        and _coerce_float(updates.get("bale_size_used")) != _coerce_float(current.get("bale_size_used"))
    )
    if raw_changed or bale_changed:
        bale = _coerce_float(merged.get("bale_size_used"))
        raw_qty = _coerce_float(merged.get("raw_qty_value")) or 0.0
        qty_col = order_row[1] if len(order_row) > 1 else None
        order_category = order_row[2] if len(order_row) > 2 else None
        detected_unit, final_qty = foparser.normalize_quantity(
            raw_qty,
            bale,
            qty_column_label=qty_col,
            category=order_category,
        )
        merged["detected_unit"] = detected_unit
        merged["final_piece_qty"] = final_qty
    elif "final_piece_qty" in updates:
        merged["final_piece_qty"] = _coerce_float(merged.get("final_piece_qty")) or 0.0

    bale = _coerce_float(merged.get("bale_size_used"))
    final_qty = _coerce_float(merged.get("final_piece_qty")) or 0.0
    merged["is_clean_bale_multiple"] = foparser.is_clean_bale_multiple(final_qty, bale)

    brand = merged.get("brand") or ""
    size = merged.get("size") or ""
    product = merged.get("product_type") or ""
    if any(k in updates for k in ("brand", "size", "product_type")):
        merged["item_key"] = "|".join(part for part in (brand, size, product) if part)

    conn.execute(
        """UPDATE filled_order_items SET
            item_key = ?, brand = ?, size = ?, product_type = ?,
            mrp = ?, ptr = ?, ex_mill_price = ?,
            bale_size_used = ?, raw_qty_value = ?, detected_unit = ?,
            final_piece_qty = ?, is_clean_bale_multiple = ?, matched = ?
           WHERE id = ? AND filled_order_id = ?""",
        (
            merged.get("item_key"),
            merged.get("brand"),
            merged.get("size"),
            merged.get("product_type"),
            merged.get("mrp"),
            merged.get("ptr"),
            merged.get("ex_mill_price"),
            merged.get("bale_size_used"),
            merged.get("raw_qty_value"),
            merged.get("detected_unit"),
            merged.get("final_piece_qty"),
            1 if merged.get("is_clean_bale_multiple") else 0,
            1 if merged.get("matched") else 0,
            item_id,
            filled_order_id,
        ),
    )
    conn.commit()
    recompute_order_counts(conn, filled_order_id)
    updated_row = conn.execute(
        f"SELECT {cols} FROM filled_order_items WHERE id = ? AND filled_order_id = ?",
        (item_id, filled_order_id),
    ).fetchone()
    return _row_to_item_dict(updated_row)


def resolve_unmatched_item(conn, user_id, filled_order_id, item_id, action, workspace_id="default", changed_by="user"):
    """action: 'skip' (leave unresolved, founder handles it manually outside
    the system) or 'add_to_article_master' (create a new Article Master entry
    from the row's own parsed brand/size/price data and link it)."""
    order_row = conn.execute(
        "SELECT category FROM filled_orders WHERE id = ? AND user_id = ?",
        (filled_order_id, user_id),
    ).fetchone()
    if order_row is None:
        raise ValueError("Filled order not found")
    category = order_row[0]

    item_row = conn.execute(
        """SELECT id, item_key, brand, size, product_type, mrp, ptr, ex_mill_price,
                  bale_size_used, matched
           FROM filled_order_items WHERE id = ? AND filled_order_id = ?""",
        (item_id, filled_order_id),
    ).fetchone()
    if item_row is None:
        raise ValueError("Item not found")

    (_id, item_key, brand, size, product_type, mrp, ptr, ex_mill_price,
     bale_size_used, matched) = item_row

    if matched:
        return {"status": "already_matched"}

    if action == "skip":
        return {"status": "skipped"}

    if action != "add_to_article_master":
        raise ValueError("action must be 'skip' or 'add_to_article_master'")

    article_data = {
        "category": category,
        "product_type": product_type,
        "brand": brand,
        "size": size,
        "mrp": mrp,
        "ptr": ptr,
        "ex_mill_price": ex_mill_price,
        "bale_pack_size": bale_size_used,
        "item_key": item_key,
        "extra_attributes": {},
    }
    article, _created, _changed = amdb.upsert_article(
        conn, user_id, article_data,
        source_filename="filled_order_resolve_unmatched",
        workspace_id=workspace_id, changed_by=changed_by,
    )
    conn.execute(
        """UPDATE filled_order_items
           SET article_id = ?, matched = 1, mrp = ?, ptr = ?, ex_mill_price = ?
           WHERE id = ?""",
        (article["id"], article["mrp"], article["ptr"], article["ex_mill_price"], item_id),
    )
    conn.commit()
    recompute_order_counts(conn, filled_order_id)
    return {"status": "added_to_article_master", "article": article}


def get_latest_filled_order(conn, user_id, distributor_id, season=None):
    orders = list_filled_orders(conn, user_id, distributor_id=distributor_id, season=season)
    return orders[0] if orders else None


def link_filled_order_to_tracking(conn, filled_order_id, tracking_id):
    conn.execute(
        """INSERT INTO filled_order_so_link (filled_order_id, order_lifecycle_tracking_id)
           VALUES (?, ?)
           ON CONFLICT(filled_order_id, order_lifecycle_tracking_id) DO NOTHING""",
        (filled_order_id, tracking_id),
    )
    conn.commit()


def get_filled_order_id_for_tracking(conn, tracking_id):
    row = conn.execute(
        """SELECT filled_order_id FROM filled_order_so_link
           WHERE order_lifecycle_tracking_id = ?
           ORDER BY linked_at DESC LIMIT 1""",
        (tracking_id,),
    ).fetchone()
    return row[0] if row else None


def list_tracking_links_for_filled_order(conn, filled_order_id):
    rows = conn.execute(
        """SELECT order_lifecycle_tracking_id, linked_at
           FROM filled_order_so_link WHERE filled_order_id = ?
           ORDER BY linked_at DESC""",
        (filled_order_id,),
    ).fetchall()
    return [
        {"tracking_id": row[0], "linked_at": row[1]}
        for row in rows
    ]


def list_so_candidates_for_filled_order(conn, user_id, filled_order_id, workspace_id):
    order = get_filled_order(conn, user_id, filled_order_id)
    if not order or not order.get("distributor_id"):
        return []
    rows = conn.execute(
        """SELECT tracking_id, order_ref_no, sales_order_file_reference, order_received_date
           FROM order_lifecycle_tracking
           WHERE distributor_id = ? AND workspace_id = ?
             AND sales_order_file_reference IS NOT NULL
           ORDER BY tracking_id DESC
           LIMIT 25""",
        (order["distributor_id"], workspace_id),
    ).fetchall()
    linked_ids = {
        link["tracking_id"]
        for link in list_tracking_links_for_filled_order(conn, filled_order_id)
    }
    candidates = []
    for tracking_id, order_ref_no, so_ref, received in rows:
        candidates.append({
            "tracking_id": tracking_id,
            "order_ref_no": order_ref_no,
            "sales_order_file_reference": so_ref,
            "order_received_date": received,
            "already_linked": tracking_id in linked_ids,
            "suggested": tracking_id == rows[0][0] and tracking_id not in linked_ids,
        })
    return candidates

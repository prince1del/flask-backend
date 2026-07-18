"""
Article Master — DB access layer (per-user isolation)

Each login owns its own catalog via user_id. workspace_id is stored
for reference and a future optional shared-workspace mode.
"""

import json
import os
import sqlite3
from datetime import datetime

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "article_master_schema.sql")
_schema_sql_cache = None

DEFAULT_BRAND_ALIASES = (
    ("Blumen", "Bluman"),
    ("Bluemen", "Bluman"),
)

ARTICLE_MASTER_COLUMNS = [
    "id", "user_id", "workspace_id", "category", "product_type", "brand", "size",
    "mrp", "ptr", "ex_mill_price", "bale_pack_size", "season_tag",
    "item_key", "extra_attributes", "is_active", "source_filename",
    "created_at", "updated_at",
]

CATEGORY_MASTER_COLUMNS = [
    "id", "user_id", "workspace_id", "category_name", "key_fields", "is_confirmed", "created_at",
]


def _row_to_article_dict(row):
    d = dict(zip(ARTICLE_MASTER_COLUMNS, row))
    d["extra_attributes"] = json.loads(d["extra_attributes"] or "{}")
    d["is_active"] = bool(d["is_active"])
    return d


def _row_to_category_dict(row):
    d = dict(zip(CATEGORY_MASTER_COLUMNS, row))
    d["key_fields"] = json.loads(d["key_fields"] or "[]")
    d["is_confirmed"] = bool(d["is_confirmed"])
    return d


NUMERIC_FIELDS = {"mrp", "ptr", "ex_mill_price"}


def _values_equal(field, old_val, new_val):
    if old_val is None or new_val is None:
        return old_val == new_val
    if field in NUMERIC_FIELDS:
        try:
            return abs(float(old_val) - float(new_val)) < 0.005
        except (TypeError, ValueError):
            return str(old_val).strip() == str(new_val).strip()
    return str(old_val).strip() == str(new_val).strip()


def ensure_schema(conn):
    """Idempotent — safe on every Article Master request."""
    global _schema_sql_cache
    if _schema_sql_cache is None:
        with open(_SCHEMA_PATH, encoding="utf-8") as f:
            _schema_sql_cache = f.read()
    conn.executescript(_schema_sql_cache)


def get_brand_alias_map(conn, user_id):
    rows = conn.execute(
        "SELECT alias, canonical_brand FROM brand_aliases WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    return {str(alias).strip().lower(): str(canonical).strip() for alias, canonical in rows}


def canonicalize_brand_name(brand, alias_map):
    if brand is None or str(brand).strip() == "":
        return brand
    stripped = str(brand).strip()
    return alias_map.get(stripped.lower(), stripped)


def ensure_default_brand_aliases(conn, user_id):
    for alias, canonical in DEFAULT_BRAND_ALIASES:
        conn.execute(
            """INSERT INTO brand_aliases (user_id, alias, canonical_brand)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id, alias) DO UPDATE SET canonical_brand = excluded.canonical_brand""",
            (user_id, alias, canonical),
        )
    conn.commit()


def apply_brand_aliases_to_articles(conn, user_id, articles, key_fields_lookup, default_key_fields):
    """Normalize brand spellings and rebuild item_key after alias resolution."""
    from article_master_parser import build_item_key

    ensure_default_brand_aliases(conn, user_id)
    alias_map = get_brand_alias_map(conn, user_id)
    for article in articles:
        brand = article.get("brand")
        if brand:
            article["brand"] = canonicalize_brand_name(brand, alias_map)
        key_fields = key_fields_lookup.get(article["category"], default_key_fields)
        core_fields = _article_core_fields(article)
        article["item_key"] = build_item_key(
            core_fields, article.get("extra_attributes") or {}, key_fields,
        )
    return articles


def list_brand_aliases(conn, user_id):
    rows = conn.execute(
        "SELECT id, alias, canonical_brand, created_at FROM brand_aliases WHERE user_id = ? ORDER BY alias",
        (user_id,),
    ).fetchall()
    cols = ["id", "alias", "canonical_brand", "created_at"]
    return [dict(zip(cols, row)) for row in rows]


def upsert_brand_alias(conn, user_id, alias, canonical_brand):
    alias = str(alias or "").strip()
    canonical_brand = str(canonical_brand or "").strip()
    if not alias or not canonical_brand:
        raise ValueError("alias and canonical_brand are required")
    conn.execute(
        """INSERT INTO brand_aliases (user_id, alias, canonical_brand)
           VALUES (?, ?, ?)
           ON CONFLICT(user_id, alias) DO UPDATE SET canonical_brand = excluded.canonical_brand""",
        (user_id, alias, canonical_brand),
    )
    conn.commit()


def _articles_identity_match(article_a, article_b, key_fields, alias_map=None):
    from article_master_parser import (
        brands_match_fuzzy,
        extract_key_field_value,
        normalize_key_part_value,
    )

    alias_map = alias_map or {}
    if article_a.get("category") != article_b.get("category"):
        return False
    core_a = _article_core_fields(article_a)
    core_b = _article_core_fields(article_b)
    extra_a = article_a.get("extra_attributes") or {}
    extra_b = article_b.get("extra_attributes") or {}
    for field in key_fields:
        field_l = field.lower()
        val_a = extract_key_field_value(field, core_a, extra_a)
        val_b = extract_key_field_value(field, core_b, extra_b)
        if field_l == "brand":
            canon_a = canonicalize_brand_name(val_a, alias_map)
            canon_b = canonicalize_brand_name(val_b, alias_map)
            if not brands_match_fuzzy(canon_a, canon_b):
                return False
            continue
        norm_a = normalize_key_part_value(field, val_a)
        norm_b = normalize_key_part_value(field, val_b)
        if norm_a and norm_b and norm_a != norm_b:
            return False
    return True


def find_duplicate_groups(conn, user_id, key_fields_lookup, default_key_fields=None):
    """Groups of articles that are the same product (brand+TC+size) under different keys."""
    default_key_fields = default_key_fields or ["brand", "size"]
    ensure_default_brand_aliases(conn, user_id)
    alias_map = get_brand_alias_map(conn, user_id)
    articles = get_all_articles(conn, user_id)
    groups = []
    used_ids = set()

    for i, article in enumerate(articles):
        if article["id"] in used_ids:
            continue
        key_fields = key_fields_lookup.get(article["category"], default_key_fields)
        group = [article]
        for other in articles[i + 1:]:
            if other["id"] in used_ids:
                continue
            other_key_fields = key_fields_lookup.get(other["category"], default_key_fields)
            if key_fields != other_key_fields:
                continue
            if _articles_identity_match(article, other, key_fields, alias_map):
                group.append(other)
                used_ids.add(other["id"])
        if len(group) > 1:
            used_ids.add(article["id"])
            group.sort(key=lambda a: a.get("created_at") or "")
            newest = max(group, key=lambda a: a.get("updated_at") or a.get("created_at") or "")
            groups.append({
                "category": article["category"],
                "identity_label": " | ".join(
                    x for x in [article.get("brand"), article.get("size")] if x
                ),
                "articles": group,
                "suggested_keep_id": group[0]["id"],
                "suggested_price_from_id": newest["id"],
            })
    return groups


def merge_articles(
    conn,
    user_id,
    keep_id,
    remove_ids,
    price_from_id=None,
    changed_by="merge",
):
    """Merge duplicate rows into keep_id; optionally copy prices from price_from_id."""
    if not remove_ids:
        raise ValueError("remove_ids is required")
    remove_ids = [int(rid) for rid in remove_ids if int(rid) != int(keep_id)]
    if not remove_ids:
        raise ValueError("No duplicate rows to remove")

    cols = ", ".join(ARTICLE_MASTER_COLUMNS)
    keep_row = conn.execute(
        f"SELECT {cols} FROM article_master WHERE id = ? AND user_id = ?",
        (keep_id, user_id),
    ).fetchone()
    if keep_row is None:
        raise ValueError("Keep article not found")
    keep_article = _row_to_article_dict(keep_row)

    price_source = keep_article
    if price_from_id and int(price_from_id) != int(keep_id):
        src_row = conn.execute(
            f"SELECT {cols} FROM article_master WHERE id = ? AND user_id = ?",
            (price_from_id, user_id),
        ).fetchone()
        if src_row is None:
            raise ValueError("Price source article not found")
        price_source = _row_to_article_dict(src_row)

    categories = get_all_categories(conn, user_id)
    key_fields_lookup = {c["category_name"]: c["key_fields"] for c in categories}
    key_fields = key_fields_lookup.get(keep_article["category"], ["brand", "size"])

    merged_updates = {
        "brand": canonicalize_brand_name(
            price_source.get("brand"), get_brand_alias_map(conn, user_id),
        ),
        "size": price_source.get("size"),
        "product_type": price_source.get("product_type"),
        "mrp": price_source.get("mrp"),
        "ptr": price_source.get("ptr"),
        "ex_mill_price": price_source.get("ex_mill_price"),
        "bale_pack_size": price_source.get("bale_pack_size"),
    }

    removed_count = 0
    for remove_id in remove_ids:
        row = conn.execute(
            "SELECT id FROM article_master WHERE id = ? AND user_id = ?",
            (remove_id, user_id),
        ).fetchone()
        if row is None:
            continue
        conn.execute("DELETE FROM article_price_history WHERE article_id = ?", (remove_id,))
        conn.execute("DELETE FROM article_master WHERE id = ? AND user_id = ?", (remove_id, user_id))
        removed_count += 1
    conn.commit()

    updated, _ = update_article_full(
        conn, user_id, keep_id, merged_updates, key_fields, changed_by=changed_by,
    )
    return updated, removed_count


def replace_article_and_consolidate(
    conn,
    user_id,
    existing_id,
    article_data,
    key_fields,
    source_filename=None,
    workspace_id="default",
    changed_by="order_sheet_upload",
):
    """Replace one row with upload data and remove other duplicate identity rows."""
    core_fields = _article_core_fields(article_data)
    extra = article_data.get("extra_attributes") or {}
    matches = find_identity_matches(
        conn, user_id, article_data["category"], core_fields, extra, key_fields,
    )
    duplicate_ids = [m["id"] for m in matches if m["id"] != int(existing_id)]

    updated, _ = replace_article_from_upload(
        conn, user_id, existing_id, article_data,
        source_filename=source_filename,
        workspace_id=workspace_id,
        changed_by=changed_by,
    )

    removed = 0
    for remove_id in duplicate_ids:
        row = conn.execute(
            "SELECT id FROM article_master WHERE id = ? AND user_id = ?",
            (remove_id, user_id),
        ).fetchone()
        if row is None:
            continue
        conn.execute("DELETE FROM article_price_history WHERE article_id = ?", (remove_id,))
        conn.execute("DELETE FROM article_master WHERE id = ? AND user_id = ?", (remove_id, user_id))
        removed += 1
    if removed:
        conn.commit()
    return updated, removed


def normalize_catalog_brand_names(conn, user_id, key_fields_lookup, default_key_fields=None):
    """Rename alias spellings (Blumen/Bluemen) to canonical Bluman and rebuild item_key."""
    from article_master_parser import build_item_key

    default_key_fields = default_key_fields or ["brand", "size"]
    ensure_default_brand_aliases(conn, user_id)
    alias_map = get_brand_alias_map(conn, user_id)
    updated_count = 0
    for article in get_all_articles(conn, user_id):
        canon_brand = canonicalize_brand_name(article.get("brand"), alias_map)
        if not canon_brand or canon_brand == article.get("brand"):
            continue
        key_fields = key_fields_lookup.get(article["category"], default_key_fields)
        merged = {**article, "brand": canon_brand}
        core = _article_core_fields(merged)
        new_key = build_item_key(core, merged.get("extra_attributes") or {}, key_fields)
        if new_key == article["item_key"] and canon_brand == article.get("brand"):
            continue
        clash = get_article_by_item_key(conn, user_id, new_key)
        if clash and clash["id"] != article["id"]:
            continue
        try:
            update_article_full(
                conn, user_id, article["id"], {"brand": canon_brand},
                key_fields, changed_by="brand_normalize",
            )
            updated_count += 1
        except ValueError:
            continue
    return updated_count


def get_category_by_name(conn, user_id, category_name):
    cols = ", ".join(CATEGORY_MASTER_COLUMNS)
    row = conn.execute(
        f"SELECT {cols} FROM category_master WHERE user_id = ? AND category_name = ?",
        (user_id, category_name),
    ).fetchone()
    return _row_to_category_dict(row) if row else None


def get_all_categories(conn, user_id, confirmed_only=False):
    cols = ", ".join(CATEGORY_MASTER_COLUMNS)
    query = f"SELECT {cols} FROM category_master WHERE user_id = ?"
    params = [user_id]
    if confirmed_only:
        query += " AND is_confirmed = 1"
    rows = conn.execute(query, params).fetchall()
    return [_row_to_category_dict(r) for r in rows]


def create_category(
    conn,
    user_id,
    category_name,
    key_fields=None,
    is_confirmed=False,
    workspace_id="default",
):
    key_fields = key_fields or ["brand", "size"]
    conn.execute(
        """INSERT INTO category_master (user_id, workspace_id, category_name, key_fields, is_confirmed)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, workspace_id, category_name, json.dumps(key_fields), 1 if is_confirmed else 0),
    )
    conn.commit()
    return get_category_by_name(conn, user_id, category_name)


def confirm_category(conn, user_id, category_name):
    conn.execute(
        "UPDATE category_master SET is_confirmed = 1 WHERE user_id = ? AND category_name = ?",
        (user_id, category_name),
    )
    conn.commit()


def get_article_by_item_key(conn, user_id, item_key):
    cols = ", ".join(ARTICLE_MASTER_COLUMNS)
    row = conn.execute(
        f"SELECT {cols} FROM article_master WHERE user_id = ? AND item_key = ?",
        (user_id, item_key),
    ).fetchone()
    return _row_to_article_dict(row) if row else None


def resolve_article_match(conn, user_id, category, core_fields, extra_attributes, key_fields):
    """
    Match a parsed row to Article Master. Tries exact item_key first, then
    rebuilds normalized keys for all articles in the category (handles TC
    suffix drift like '104' vs '104 (ONE IN A DENT)'), then fuzzy brand
    matching for distributor typos (e.g. Blumen vs Bluemen).
    """
    from article_master_parser import (
        brands_match_fuzzy,
        build_item_key,
        extract_key_field_value,
        normalize_key_part_value,
    )

    alias_map = get_brand_alias_map(conn, user_id)
    target_key = build_item_key(core_fields, extra_attributes, key_fields)
    article = get_article_by_item_key(conn, user_id, target_key)
    if article:
        return article

    article_core_fields = ("brand", "size", "product_type", "mrp", "ptr", "ex_mill_price", "bale_pack_size")
    candidates_in_category = get_articles_by_category(conn, user_id, category)
    for candidate in candidates_in_category:
        cand_core = {f: candidate.get(f) for f in article_core_fields}
        cand_key = build_item_key(cand_core, candidate.get("extra_attributes") or {}, key_fields)
        if cand_key == target_key:
            return candidate

    fuzzy_candidates = []
    for candidate in candidates_in_category:
        cand_core = {f: candidate.get(f) for f in article_core_fields}
        cand_extra = candidate.get("extra_attributes") or {}
        matches = True
        for field in key_fields:
            field_l = field.lower()
            file_val = extract_key_field_value(field, core_fields, extra_attributes)
            cand_val = extract_key_field_value(field, cand_core, cand_extra)
            if field_l == "brand":
                file_brand = canonicalize_brand_name(file_val, alias_map)
                cand_brand = canonicalize_brand_name(cand_val, alias_map)
                if not brands_match_fuzzy(file_brand, cand_brand):
                    matches = False
                    break
                continue
            n_file = normalize_key_part_value(field, file_val)
            n_cand = normalize_key_part_value(field, cand_val)
            if n_file and n_cand and n_file != n_cand:
                matches = False
                break
            if n_file and not n_cand:
                matches = False
                break
            # Distributor order files often omit TC when brand+size are enough.
            if not n_file and n_cand and field_l != "tc":
                matches = False
                break
        if matches:
            fuzzy_candidates.append(candidate)

    if len(fuzzy_candidates) == 1:
        return fuzzy_candidates[0]
    if len(fuzzy_candidates) > 1:
        # Same product stored twice (e.g. Blumen + Bluemen) — use oldest row.
        fuzzy_candidates.sort(key=lambda c: c["id"])
        return fuzzy_candidates[0]
    return None


def get_articles_by_category(conn, user_id, category, active_only=True):
    cols = ", ".join(ARTICLE_MASTER_COLUMNS)
    query = f"SELECT {cols} FROM article_master WHERE user_id = ? AND category = ?"
    params = [user_id, category]
    if active_only:
        query += " AND is_active = 1"
    query += " ORDER BY LOWER(COALESCE(brand, '')), LOWER(COALESCE(size, '')), id"
    rows = conn.execute(query, params).fetchall()
    return _attach_history_flags(conn, [_row_to_article_dict(r) for r in rows])


def get_all_articles(conn, user_id, active_only=True):
    cols = ", ".join(ARTICLE_MASTER_COLUMNS)
    query = f"SELECT {cols} FROM article_master WHERE user_id = ?"
    params = [user_id]
    if active_only:
        query += " AND is_active = 1"
    query += " ORDER BY LOWER(COALESCE(brand, '')), LOWER(COALESCE(size, '')), id"
    rows = conn.execute(query, params).fetchall()
    return _attach_history_flags(conn, [_row_to_article_dict(r) for r in rows])


def _attach_history_flags(conn, articles):
    if not articles:
        return articles
    ids = [a["id"] for a in articles]
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT DISTINCT article_id FROM article_price_history WHERE article_id IN ({placeholders})",
        tuple(ids),
    ).fetchall()
    with_history = {r[0] for r in rows}
    for article in articles:
        article["has_price_history"] = article["id"] in with_history
    return articles


TRACKED_PRICE_FIELDS = ("mrp", "ptr", "ex_mill_price", "bale_pack_size")


def get_price_field_diffs(existing, article_data):
    """Return tracked price/pack fields that differ between existing and upload row."""
    diffs = []
    for field in TRACKED_PRICE_FIELDS:
        new_val = article_data.get(field)
        if new_val is not None and not _values_equal(field, existing.get(field), new_val):
            diffs.append(field)
    return diffs


def _article_core_fields(article_data):
    return {
        "brand": article_data.get("brand"),
        "size": article_data.get("size"),
        "product_type": article_data.get("product_type"),
        "mrp": article_data.get("mrp"),
        "ptr": article_data.get("ptr"),
        "ex_mill_price": article_data.get("ex_mill_price"),
        "bale_pack_size": article_data.get("bale_pack_size"),
    }


def find_identity_matches(conn, user_id, category, core_fields, extra_attributes, key_fields):
    """All Article Master rows that are the same product (brand+TC+size)."""
    ensure_default_brand_aliases(conn, user_id)
    alias_map = get_brand_alias_map(conn, user_id)
    probe = {
        "category": category,
        "brand": core_fields.get("brand"),
        "size": core_fields.get("size"),
        "product_type": core_fields.get("product_type"),
        "extra_attributes": extra_attributes or {},
    }
    matches = []
    for candidate in get_articles_by_category(conn, user_id, category):
        if _articles_identity_match(probe, candidate, key_fields, alias_map):
            matches.append(candidate)
    matches.sort(key=lambda a: a["id"])
    return matches


def classify_upload_article(conn, user_id, article_data, key_fields):
    """
    Decide how one parsed upload row should be handled.

    Returns dict with:
      action: "create" | "skip" | "conflict"
      existing: matched article dict or None
      price_diffs: list of differing price fields
      conflict_reason: short code when action is "conflict"
      duplicate_ids: other row ids for the same product (if any)
    """
    ensure_default_brand_aliases(conn, user_id)
    item_key = article_data["item_key"]
    category = article_data["category"]
    core_fields = _article_core_fields(article_data)
    extra = article_data.get("extra_attributes") or {}

    matches = find_identity_matches(conn, user_id, category, core_fields, extra, key_fields)

    if not matches:
        return {
            "action": "create",
            "existing": None,
            "price_diffs": [],
            "conflict_reason": None,
            "duplicate_ids": [],
        }

    exact = next((m for m in matches if m["item_key"] == item_key), None)
    existing = exact or matches[0]
    duplicate_ids = [m["id"] for m in matches if m["id"] != existing["id"]]
    price_diffs = get_price_field_diffs(existing, article_data)
    key_differs = existing["item_key"] != item_key
    has_duplicate_rows = len(matches) > 1

    if not price_diffs and not key_differs and not has_duplicate_rows:
        return {
            "action": "skip",
            "existing": existing,
            "price_diffs": [],
            "conflict_reason": None,
            "duplicate_ids": [],
        }

    reason = "duplicate_entries_in_master" if has_duplicate_rows else (
        "price_and_key_mismatch" if (price_diffs and key_differs) else (
            "item_key_mismatch" if key_differs else "price_mismatch"
        )
    )
    return {
        "action": "conflict",
        "existing": existing,
        "price_diffs": price_diffs,
        "conflict_reason": reason,
        "duplicate_ids": duplicate_ids,
    }


def _record_price_changes(conn, existing, article_data, changed_by, now):
    changed_fields = []
    for field in TRACKED_PRICE_FIELDS:
        old_val = existing.get(field)
        new_val = article_data.get(field, old_val)
        if new_val is not None and not _values_equal(field, old_val, new_val):
            conn.execute(
                """INSERT INTO article_price_history
                   (article_id, field_changed, old_value, new_value, changed_by, changed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (existing["id"], field, str(old_val), str(new_val), changed_by, now),
            )
            changed_fields.append(field)
    return changed_fields


def insert_article(
    conn,
    user_id,
    article_data,
    source_filename=None,
    workspace_id="default",
):
    """Insert a new article row (caller must ensure item_key is unique)."""
    now = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO article_master
           (user_id, workspace_id, category, product_type, brand, size, mrp, ptr, ex_mill_price,
            bale_pack_size, season_tag, item_key, extra_attributes, is_active,
            source_filename, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
        (
            user_id,
            workspace_id,
            article_data["category"],
            article_data.get("product_type"),
            article_data.get("brand"),
            article_data.get("size"),
            article_data.get("mrp"),
            article_data.get("ptr"),
            article_data.get("ex_mill_price"),
            article_data.get("bale_pack_size"),
            article_data.get("season_tag"),
            article_data["item_key"],
            json.dumps(article_data.get("extra_attributes", {})),
            source_filename,
            now,
            now,
        ),
    )
    conn.commit()
    return get_article_by_item_key(conn, user_id, article_data["item_key"])


def replace_article_from_upload(
    conn,
    user_id,
    existing_id,
    article_data,
    source_filename=None,
    workspace_id="default",
    changed_by="order_sheet_upload",
):
    """Update an existing article with upload data (prices, item_key, extras)."""
    cols = ", ".join(ARTICLE_MASTER_COLUMNS)
    row = conn.execute(
        f"SELECT {cols} FROM article_master WHERE id = ? AND user_id = ?",
        (existing_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError("Article not found")

    existing = _row_to_article_dict(row)
    now = datetime.utcnow().isoformat()
    changed_fields = _record_price_changes(conn, existing, article_data, changed_by, now)
    merged_extra = {**existing["extra_attributes"], **article_data.get("extra_attributes", {})}

    conn.execute(
        """UPDATE article_master SET
           category = ?, product_type = ?, brand = ?, size = ?, mrp = ?, ptr = ?, ex_mill_price = ?,
           bale_pack_size = ?, season_tag = ?, item_key = ?, extra_attributes = ?,
           source_filename = ?, updated_at = ?
           WHERE id = ? AND user_id = ?""",
        (
            article_data.get("category", existing["category"]),
            article_data.get("product_type", existing["product_type"]),
            article_data.get("brand", existing["brand"]),
            article_data.get("size", existing["size"]),
            article_data.get("mrp", existing["mrp"]),
            article_data.get("ptr", existing["ptr"]),
            article_data.get("ex_mill_price", existing["ex_mill_price"]),
            article_data.get("bale_pack_size", existing["bale_pack_size"]),
            article_data.get("season_tag", existing["season_tag"]),
            article_data["item_key"],
            json.dumps(merged_extra),
            source_filename,
            now,
            existing_id,
            user_id,
        ),
    )
    conn.commit()
    updated = get_article_by_item_key(conn, user_id, article_data["item_key"])
    return updated, changed_fields


def upsert_article(
    conn,
    user_id,
    article_data,
    source_filename=None,
    workspace_id="default",
    changed_by="order_sheet_upload",
):
    existing = get_article_by_item_key(conn, user_id, article_data["item_key"])
    now = datetime.utcnow().isoformat()

    if existing is None:
        conn.execute(
            """INSERT INTO article_master
               (user_id, workspace_id, category, product_type, brand, size, mrp, ptr, ex_mill_price,
                bale_pack_size, season_tag, item_key, extra_attributes, is_active,
                source_filename, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                user_id,
                workspace_id,
                article_data["category"],
                article_data.get("product_type"),
                article_data.get("brand"),
                article_data.get("size"),
                article_data.get("mrp"),
                article_data.get("ptr"),
                article_data.get("ex_mill_price"),
                article_data.get("bale_pack_size"),
                article_data.get("season_tag"),
                article_data["item_key"],
                json.dumps(article_data.get("extra_attributes", {})),
                source_filename,
                now,
                now,
            ),
        )
        conn.commit()
        return get_article_by_item_key(conn, user_id, article_data["item_key"]), True, []

    tracked_fields = ["mrp", "ptr", "ex_mill_price", "bale_pack_size"]
    changed_fields = []
    for field in tracked_fields:
        old_val = existing.get(field)
        new_val = article_data.get(field, old_val)
        if new_val is not None and not _values_equal(field, old_val, new_val):
            conn.execute(
                """INSERT INTO article_price_history
                   (article_id, field_changed, old_value, new_value, changed_by, changed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (existing["id"], field, str(old_val), str(new_val), changed_by, now),
            )
            changed_fields.append(field)

    merged_extra = {**existing["extra_attributes"], **article_data.get("extra_attributes", {})}

    conn.execute(
        """UPDATE article_master SET
           product_type = ?, brand = ?, size = ?, mrp = ?, ptr = ?, ex_mill_price = ?,
           bale_pack_size = ?, season_tag = ?, extra_attributes = ?, source_filename = ?,
           updated_at = ?
           WHERE id = ? AND user_id = ?""",
        (
            article_data.get("product_type", existing["product_type"]),
            article_data.get("brand", existing["brand"]),
            article_data.get("size", existing["size"]),
            article_data.get("mrp", existing["mrp"]),
            article_data.get("ptr", existing["ptr"]),
            article_data.get("ex_mill_price", existing["ex_mill_price"]),
            article_data.get("bale_pack_size", existing["bale_pack_size"]),
            article_data.get("season_tag", existing["season_tag"]),
            json.dumps(merged_extra),
            source_filename,
            now,
            existing["id"],
            user_id,
        ),
    )
    conn.commit()
    return get_article_by_item_key(conn, user_id, article_data["item_key"]), False, changed_fields


def manual_edit_article(conn, user_id, article_id, field, new_value, changed_by="user"):
    allowed_fields = {"mrp", "ptr", "ex_mill_price", "bale_pack_size"}
    if field not in allowed_fields:
        raise ValueError(f"Field '{field}' is not editable. Allowed: {allowed_fields}")

    cols = ", ".join(ARTICLE_MASTER_COLUMNS)
    row = conn.execute(
        f"SELECT {cols} FROM article_master WHERE id = ? AND user_id = ?",
        (article_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError("Article not found")

    existing = _row_to_article_dict(row)
    old_value = existing[field]
    if _values_equal(field, old_value, new_value):
        return existing

    now = datetime.utcnow().isoformat()
    conn.execute(
        f"UPDATE article_master SET {field} = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (new_value, now, article_id, user_id),
    )
    conn.execute(
        """INSERT INTO article_price_history
           (article_id, field_changed, old_value, new_value, changed_by, changed_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (article_id, field, str(old_value), str(new_value), changed_by, now),
    )
    conn.commit()
    return get_article_by_item_key(conn, user_id, existing["item_key"])


FULL_EDITABLE_FIELDS = ("brand", "size", "product_type", "mrp", "ptr", "ex_mill_price", "bale_pack_size")
PRICE_HISTORY_FIELDS = {"mrp", "ptr", "ex_mill_price", "bale_pack_size"}


def update_article_full(conn, user_id, article_id, updates, key_fields, changed_by="user"):
    """Update multiple article fields; rebuilds item_key and records price history."""
    from article_master_parser import build_item_key

    if not updates:
        raise ValueError("No fields to update")

    unknown = set(updates.keys()) - set(FULL_EDITABLE_FIELDS)
    if unknown:
        raise ValueError(f"Fields not editable: {unknown}")

    cols = ", ".join(ARTICLE_MASTER_COLUMNS)
    row = conn.execute(
        f"SELECT {cols} FROM article_master WHERE id = ? AND user_id = ?",
        (article_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError("Article not found")

    existing = _row_to_article_dict(row)
    merged = {**existing, **updates}
    core_fields = _article_core_fields(merged)
    new_item_key = build_item_key(core_fields, merged.get("extra_attributes") or {}, key_fields)

    if new_item_key != existing["item_key"]:
        clash = get_article_by_item_key(conn, user_id, new_item_key)
        if clash and clash["id"] != article_id:
            raise ValueError(f"Another article already uses item key '{new_item_key}'")

    now = datetime.utcnow().isoformat()
    changed_price_fields = []
    for field in PRICE_HISTORY_FIELDS:
        if field not in updates:
            continue
        old_val = existing.get(field)
        new_val = updates[field]
        if not _values_equal(field, old_val, new_val):
            conn.execute(
                """INSERT INTO article_price_history
                   (article_id, field_changed, old_value, new_value, changed_by, changed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (article_id, field, str(old_val), str(new_val), changed_by, now),
            )
            changed_price_fields.append(field)

    conn.execute(
        """UPDATE article_master SET
           brand = ?, size = ?, product_type = ?, mrp = ?, ptr = ?, ex_mill_price = ?,
           bale_pack_size = ?, item_key = ?, updated_at = ?
           WHERE id = ? AND user_id = ?""",
        (
            merged.get("brand"),
            merged.get("size"),
            merged.get("product_type"),
            merged.get("mrp"),
            merged.get("ptr"),
            merged.get("ex_mill_price"),
            merged.get("bale_pack_size"),
            new_item_key,
            now,
            article_id,
            user_id,
        ),
    )
    conn.commit()
    updated = get_article_by_item_key(conn, user_id, new_item_key)
    if updated:
        _attach_history_flags(conn, [updated])
    return updated, changed_price_fields


def get_price_history(conn, article_id, user_id):
    row = conn.execute(
        "SELECT id FROM article_master WHERE id = ? AND user_id = ?",
        (article_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError("Article not found")

    rows = conn.execute(
        """SELECT id, article_id, field_changed, old_value, new_value, changed_by, changed_at
           FROM article_price_history WHERE article_id = ? ORDER BY changed_at DESC""",
        (article_id,),
    ).fetchall()
    cols = ["id", "article_id", "field_changed", "old_value", "new_value", "changed_by", "changed_at"]
    return [dict(zip(cols, r)) for r in rows]


def delete_article(conn, user_id, article_id):
    row = conn.execute(
        "SELECT id FROM article_master WHERE id = ? AND user_id = ?",
        (article_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError("Article not found")
    conn.execute("DELETE FROM article_price_history WHERE article_id = ?", (article_id,))
    conn.execute("DELETE FROM article_master WHERE id = ? AND user_id = ?", (article_id, user_id))
    conn.commit()
    return True


def delete_all_articles(conn, user_id, category=None):
    """Delete all articles for this user. Optional category filter (Bed/Bath/TOB/...)."""
    if category and category != "All":
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM article_master WHERE user_id = ? AND category = ?",
                (user_id, category),
            ).fetchall()
        ]
    else:
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM article_master WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        ]

    if not ids:
        return 0

    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"DELETE FROM article_price_history WHERE article_id IN ({placeholders})",
        ids,
    )
    if category and category != "All":
        conn.execute(
            "DELETE FROM article_master WHERE user_id = ? AND category = ?",
            (user_id, category),
        )
    else:
        conn.execute("DELETE FROM article_master WHERE user_id = ?", (user_id,))
    conn.commit()
    return len(ids)

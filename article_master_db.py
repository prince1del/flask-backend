"""
Article Master — DB access layer (per-user isolation)

Each login owns its own catalog via user_id. workspace_id is stored
for reference and a future optional shared-workspace mode.
"""

import json
import os
import sqlite3
from datetime import datetime

import article_master_parser as amparser

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "article_master_schema.sql")
_schema_sql_cache = None

DEFAULT_BRAND_ALIASES = (
    # Correct spelling is Blumen; Bluemen/Bluman are distributor typos.
    ("Bluemen", "Blumen"),
    ("Bluman", "Blumen"),
    ("BLUMEN", "Blumen"),
    ("BLUEMEN", "Blumen"),
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
    d["extra_attributes"] = amparser.strip_excluded_extra_attributes(
        json.loads(d["extra_attributes"] or "{}")
    )
    d["is_active"] = bool(d["is_active"])
    # Display/API: resolve Product from Size only. Category is persisted by repair/upload — never fake it here.
    d["product_type"] = amparser.resolve_product_type(d.get("product_type"), d.get("size"))
    return d


def repair_product_types_from_size(conn, user_id):
    """Persist Size→Product/Category (comforter size → Product Comforter + Category TOB)."""
    rows = conn.execute(
        "SELECT id, category, product_type, size FROM article_master WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    updated = 0
    now = datetime.utcnow().isoformat()
    for article_id, category, product_type, size in rows:
        new_product = amparser.resolve_product_type(product_type, size)
        new_category = amparser.resolve_category(category, size, new_product)
        old_p = None if product_type is None else str(product_type).strip()
        new_p = None if new_product is None else str(new_product).strip()
        old_c = None if category is None else str(category).strip()
        new_c = None if new_category is None else str(new_category).strip()
        if old_p == new_p and old_c == new_c:
            continue
        conn.execute(
            """UPDATE article_master
               SET product_type = ?, category = ?, updated_at = ?
               WHERE id = ? AND user_id = ?""",
            (new_product, new_category, now, article_id, user_id),
        )
        updated += 1
    if updated:
        conn.commit()
    return updated


def _clean_extra_json(extra) -> str:
    return json.dumps(amparser.strip_excluded_extra_attributes(extra or {}))


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
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        sql = f.read()
    _schema_sql_cache = sql
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
    """Normalize brand/size spellings and rebuild item_key after alias resolution."""
    from article_master_parser import build_item_key, normalize_brand_and_size

    ensure_default_brand_aliases(conn, user_id)
    alias_map = get_brand_alias_map(conn, user_id)
    for article in articles:
        brand = article.get("brand")
        if brand:
            brand = canonicalize_brand_name(brand, alias_map)
        brand, size = normalize_brand_and_size(brand, article.get("size"))
        article["brand"] = brand
        article["size"] = size
        article["product_type"] = amparser.resolve_product_type(
            article.get("product_type"), size,
        )
        article["category"] = amparser.resolve_category(
            article.get("category"), size, article.get("product_type"),
        )
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
    # Celebrating India vs Celebrating India (BINB) must stay DIFFERENT items.
    a_l = alias.lower()
    c_l = canonical_brand.lower()
    pair = {a_l, c_l}
    if "celebrating india" in pair and "celebrating india (binb)" in pair:
        raise ValueError(
            "Celebrating India and Celebrating India (BINB) are different items — "
            "do not alias-merge them"
        )
    conn.execute(
        """INSERT INTO brand_aliases (user_id, alias, canonical_brand)
           VALUES (?, ?, ?)
           ON CONFLICT(user_id, alias) DO UPDATE SET canonical_brand = excluded.canonical_brand""",
        (user_id, alias, canonical_brand),
    )
    conn.commit()


def _articles_identity_match(article_a, article_b, key_fields, alias_map=None, ignore_category=False):
    from article_master_parser import (
        brands_match_fuzzy,
        extract_key_field_value,
        normalize_key_part_value,
    )

    alias_map = alias_map or {}
    if not ignore_category and article_a.get("category") != article_b.get("category"):
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
    """Rename alias spellings (Bluemen/Bluman) to canonical Blumen and rebuild item_key."""
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


# Standard Article Master categories — auto-created on first upload/list.
DEFAULT_CATEGORY_KEY_FIELDS = {
    "Bed": ["brand", "TC", "size"],
    "Bath": ["brand", "size", "color", "product"],
    "TOB": ["brand", "size", "product", "color"],
    "Pillow": ["brand", "size", "product"],
}


def ensure_default_categories(conn, user_id, workspace_id="default"):
    """Create Bed/Bath/TOB/Pillow if missing; upgrade taught key fields; rename TOB Pillow→Pillow."""
    existing = {c["category_name"]: c for c in get_all_categories(conn, user_id)}

    # Legacy category rename (teaching: category key is Pillow, not TOB Pillow)
    # Always migrate leftover articles; drop TOB Pillow master row when present.
    legacy = existing.get("TOB Pillow")
    conn.execute(
        "UPDATE article_master SET category = ? WHERE user_id = ? AND category = ?",
        ("Pillow", user_id, "TOB Pillow"),
    )
    if legacy and "Pillow" not in existing:
        conn.execute(
            "UPDATE category_master SET category_name = ?, key_fields = ?, is_confirmed = 1 "
            "WHERE id = ? AND user_id = ?",
            (
                "Pillow",
                json.dumps(DEFAULT_CATEGORY_KEY_FIELDS["Pillow"]),
                legacy["id"],
                user_id,
            ),
        )
    elif legacy:
        conn.execute(
            "DELETE FROM category_master WHERE id = ? AND user_id = ?",
            (legacy["id"], user_id),
        )
    conn.commit()
    existing = {c["category_name"]: c for c in get_all_categories(conn, user_id)}

    created = []
    for name, key_fields in DEFAULT_CATEGORY_KEY_FIELDS.items():
        if name not in existing:
            create_category(
                conn, user_id, name, key_fields,
                is_confirmed=True, workspace_id=workspace_id,
            )
            created.append(name)
            continue
        # Upgrade taught identity keys when they diverge from defaults
        if name in {"Bath", "TOB", "Pillow"}:
            cur = existing[name].get("key_fields") or []
            if [str(x).lower() for x in cur] != [str(x).lower() for x in key_fields]:
                conn.execute(
                    "UPDATE category_master SET key_fields = ?, is_confirmed = 1 WHERE id = ? AND user_id = ?",
                    (json.dumps(key_fields), existing[name]["id"], user_id),
                )
                conn.commit()
    return created


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
    if row is None:
        return None
    article = _row_to_article_dict(row)
    # Display invariant: core amounts always mirror latest season snapshot
    synced = sync_articles_core_prices_to_latest_season(conn, [article], persist=True)
    return synced[0] if synced else article


def get_article_by_id(conn, user_id, article_id):
    cols = ", ".join(ARTICLE_MASTER_COLUMNS)
    row = conn.execute(
        f"SELECT {cols} FROM article_master WHERE id = ? AND user_id = ?",
        (article_id, user_id),
    ).fetchone()
    if row is None:
        return None
    article = _row_to_article_dict(row)
    synced = sync_articles_core_prices_to_latest_season(conn, [article], persist=True)
    article = synced[0] if synced else article
    return _attach_history_flags(conn, [article])[0]


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
            # Distributor FO often omits TC / Color / Product when brand+size are enough.
            # Bath special-order sheets omit Shade; default product is Terry Towel.
            if not n_file and n_cand and field_l not in {"tc", "color", "colour", "product", "product_type"}:
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
    articles = _attach_history_flags(conn, [_row_to_article_dict(r) for r in rows])
    articles = sync_articles_core_prices_to_latest_season(conn, articles, persist=True)
    return amparser.sort_articles_for_display(articles)


def get_all_articles(conn, user_id, active_only=True):
    cols = ", ".join(ARTICLE_MASTER_COLUMNS)
    query = f"SELECT {cols} FROM article_master WHERE user_id = ?"
    params = [user_id]
    if active_only:
        query += " AND is_active = 1"
    query += " ORDER BY LOWER(COALESCE(brand, '')), LOWER(COALESCE(size, '')), id"
    rows = conn.execute(query, params).fetchall()
    articles = _attach_history_flags(conn, [_row_to_article_dict(r) for r in rows])
    articles = sync_articles_core_prices_to_latest_season(conn, articles, persist=True)
    return amparser.sort_articles_for_display(articles)


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


def _latest_season_snapshot_from_rows(season_rows):
    """Pick the chronologically latest non-empty season snapshot from DB rows."""
    best = None
    best_key = None
    for season_tag, mrp, ptr, ex_mill in season_rows:
        if mrp is None and ptr is None and ex_mill is None:
            continue
        tag = amparser.normalize_season_tag(season_tag) or str(season_tag or "").strip()
        key = (amparser.season_rank(tag), tag)
        if best is None or key > best_key:
            best = {
                "season_tag": tag,
                "mrp": mrp,
                "ptr": ptr,
                "ex_mill_price": ex_mill,
            }
            best_key = key
    return best


def sync_article_core_prices_to_latest_season(conn, article_id, persist=True):
    """
    Article Master list amounts must always mirror the latest season snapshot.
    Returns the latest snapshot dict or None.
    """
    rows = conn.execute(
        """SELECT season_tag, mrp, ptr, ex_mill_price
           FROM article_season_prices WHERE article_id = ?""",
        (article_id,),
    ).fetchall()
    latest = _latest_season_snapshot_from_rows(rows)
    if not latest:
        return None
    if not persist:
        return latest

    art = conn.execute(
        "SELECT mrp, ptr, ex_mill_price, season_tag FROM article_master WHERE id = ?",
        (article_id,),
    ).fetchone()
    if not art:
        return latest
    cur_mrp, cur_ptr, cur_ex, cur_tag = art
    cur_tag = amparser.normalize_season_tag(cur_tag)
    same_tag = cur_tag == latest["season_tag"]
    same_prices = (
        _values_equal("mrp", cur_mrp, latest["mrp"])
        and _values_equal("ptr", cur_ptr, latest["ptr"])
        and _values_equal("ex_mill_price", cur_ex, latest["ex_mill_price"])
    )
    if same_tag and same_prices:
        return latest
    now = datetime.utcnow().isoformat()
    conn.execute(
        """UPDATE article_master
           SET mrp = ?, ptr = ?, ex_mill_price = ?, season_tag = ?, updated_at = ?
           WHERE id = ?""",
        (
            latest["mrp"],
            latest["ptr"],
            latest["ex_mill_price"],
            latest["season_tag"],
            now,
            article_id,
        ),
    )
    return latest


def sync_articles_core_prices_to_latest_season(conn, articles, persist=True):
    """Batch overlay (+ optional persist) latest season prices onto article dicts."""
    if not articles:
        return articles
    ids = [a["id"] for a in articles if a.get("id") is not None]
    if not ids:
        return articles
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""SELECT article_id, season_tag, mrp, ptr, ex_mill_price
            FROM article_season_prices WHERE article_id IN ({placeholders})""",
        tuple(ids),
    ).fetchall()
    by_id = {}
    for article_id, season_tag, mrp, ptr, ex_mill in rows:
        by_id.setdefault(article_id, []).append((season_tag, mrp, ptr, ex_mill))

    dirty = False
    now = datetime.utcnow().isoformat()
    for article in articles:
        snaps = by_id.get(article["id"])
        if not snaps:
            continue
        latest = _latest_season_snapshot_from_rows(snaps)
        if not latest:
            continue
        cur_tag = amparser.normalize_season_tag(article.get("season_tag"))
        needs = (
            cur_tag != latest["season_tag"]
            or not _values_equal("mrp", article.get("mrp"), latest["mrp"])
            or not _values_equal("ptr", article.get("ptr"), latest["ptr"])
            or not _values_equal("ex_mill_price", article.get("ex_mill_price"), latest["ex_mill_price"])
        )
        article["mrp"] = latest["mrp"]
        article["ptr"] = latest["ptr"]
        article["ex_mill_price"] = latest["ex_mill_price"]
        article["season_tag"] = latest["season_tag"]
        if persist and needs:
            conn.execute(
                """UPDATE article_master
                   SET mrp = ?, ptr = ?, ex_mill_price = ?, season_tag = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    latest["mrp"],
                    latest["ptr"],
                    latest["ex_mill_price"],
                    latest["season_tag"],
                    now,
                    article["id"],
                ),
            )
            dirty = True
    if persist and dirty:
        conn.commit()
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
    """All Article Master rows that are the same product (brand+TC+size).

    Searches the whole catalog (not only one category) so a Bed→TOB comforter
    move updates the existing row instead of orphaning / hiding it.
    """
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
    for candidate in get_all_articles(conn, user_id, active_only=True):
        if _articles_identity_match(probe, candidate, key_fields, alias_map, ignore_category=True):
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
    core_fields = _article_core_fields(article_data)
    extra = article_data.get("extra_attributes") or {}

    exact = get_article_by_item_key(conn, user_id, item_key)
    matches = find_identity_matches(
        conn, user_id, article_data.get("category"), core_fields, extra, key_fields,
    )
    if exact and not any(m["id"] == exact["id"] for m in matches):
        matches = [exact] + matches

    if not matches:
        return {
            "action": "create",
            "existing": None,
            "price_diffs": [],
            "conflict_reason": None,
            "duplicate_ids": [],
        }

    existing = exact or next((m for m in matches if m["item_key"] == item_key), None) or matches[0]
    duplicate_ids = [m["id"] for m in matches if m["id"] != existing["id"]]
    price_diffs = get_price_field_diffs(existing, article_data)
    key_differs = existing["item_key"] != item_key
    has_duplicate_rows = len(matches) > 1
    category_differs = str(existing.get("category") or "") != str(article_data.get("category") or "")

    # Same prices but Bed→TOB (or similar): force upsert path so category/product persist.
    if category_differs and not price_diffs and not key_differs and not has_duplicate_rows:
        return {
            "action": "create",
            "existing": existing,
            "price_diffs": [],
            "conflict_reason": "category_mismatch",
            "duplicate_ids": [],
        }

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
    article_data = dict(article_data or {})
    article_data["product_type"] = amparser.resolve_product_type(
        article_data.get("product_type"), article_data.get("size"),
    )
    article_data["category"] = amparser.resolve_category(
        article_data.get("category"), article_data.get("size"), article_data.get("product_type"),
    )
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
            _clean_extra_json(article_data.get("extra_attributes", {})),
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
    """Update an existing article with upload data (prices, item_key, extras).

    Prices follow season rules: older/blank season must not clobber a newer
    season's amounts. After write, core row is synced to the latest snapshot.
    """
    article_data = dict(article_data or {})
    article_data["product_type"] = amparser.resolve_product_type(
        article_data.get("product_type"), article_data.get("size"),
    )
    article_data["category"] = amparser.resolve_category(
        article_data.get("category"), article_data.get("size"), article_data.get("product_type"),
    )
    if article_data.get("category") == "TOB Pillow":
        article_data["category"] = "Pillow"
    cols = ", ".join(ARTICLE_MASTER_COLUMNS)
    row = conn.execute(
        f"SELECT {cols} FROM article_master WHERE id = ? AND user_id = ?",
        (existing_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError("Article not found")

    existing = _row_to_article_dict(row)
    now = datetime.utcnow().isoformat()
    incoming_season = amparser.normalize_season_tag(article_data.get("season_tag"))
    if incoming_season:
        article_data = {**article_data, "season_tag": incoming_season}

    existing_latest = existing.get("season_tag")
    snap = sync_article_core_prices_to_latest_season(conn, existing_id, persist=False)
    if snap and amparser.season_rank(snap.get("season_tag")) >= amparser.season_rank(existing_latest):
        existing_latest = snap.get("season_tag")

    # Blank season never wins over a known newer season
    apply_latest = False
    if incoming_season:
        apply_latest = amparser.is_season_newer_or_equal(incoming_season, existing_latest)
    elif not existing_latest and not snap:
        apply_latest = True

    changed_fields = []
    if apply_latest:
        changed_fields = _record_price_changes(conn, existing, article_data, changed_by, now)

    merged_extra = amparser.merge_extra_attributes(
        existing["extra_attributes"],
        article_data.get("extra_attributes") or {},
        overwrite_nonblank=apply_latest,
    )

    if apply_latest:
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
                _clean_extra_json(merged_extra),
                source_filename,
                now,
                existing_id,
                user_id,
            ),
        )
    else:
        conn.execute(
            """UPDATE article_master SET
               category = COALESCE(?, category),
               product_type = COALESCE(?, product_type),
               brand = COALESCE(?, brand),
               size = COALESCE(?, size),
               bale_pack_size = COALESCE(?, bale_pack_size),
               item_key = ?,
               extra_attributes = ?,
               source_filename = COALESCE(?, source_filename),
               updated_at = ?
               WHERE id = ? AND user_id = ?""",
            (
                article_data.get("category"),
                article_data.get("product_type"),
                article_data.get("brand"),
                article_data.get("size"),
                article_data.get("bale_pack_size"),
                article_data["item_key"],
                _clean_extra_json(merged_extra),
                source_filename,
                now,
                existing_id,
                user_id,
            ),
        )

    if incoming_season:
        upsert_season_prices(
            conn, existing_id, incoming_season, article_data,
            source_filename=source_filename, changed_at=now,
        )
    sync_article_core_prices_to_latest_season(conn, existing_id, persist=True)
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
    article_data = dict(article_data or {})
    article_data["product_type"] = amparser.resolve_product_type(
        article_data.get("product_type"), article_data.get("size"),
    )
    article_data["category"] = amparser.resolve_category(
        article_data.get("category"), article_data.get("size"), article_data.get("product_type"),
    )
    if article_data.get("category") == "TOB Pillow":
        article_data["category"] = "Pillow"
    existing = get_article_by_item_key(conn, user_id, article_data["item_key"])
    now = datetime.utcnow().isoformat()
    incoming_season = amparser.normalize_season_tag(article_data.get("season_tag"))
    if incoming_season:
        article_data = {**article_data, "season_tag": incoming_season}

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
                _clean_extra_json(article_data.get("extra_attributes", {})),
                source_filename,
                now,
                now,
            ),
        )
        conn.commit()
        created = get_article_by_item_key(conn, user_id, article_data["item_key"])
        if created and incoming_season:
            upsert_season_prices(
                conn, created["id"], incoming_season, article_data,
                source_filename=source_filename, changed_at=now,
            )
            sync_article_core_prices_to_latest_season(conn, created["id"], persist=True)
            conn.commit()
            created = get_article_by_item_key(conn, user_id, article_data["item_key"])
        return created, True, []

    # Compare against effective latest (article tag or newest season snapshot)
    existing_latest = existing.get("season_tag")
    snap = sync_article_core_prices_to_latest_season(conn, existing["id"], persist=False)
    if snap and amparser.season_rank(snap.get("season_tag")) >= amparser.season_rank(existing_latest):
        existing_latest = snap.get("season_tag")

    # Blank/unknown season must never overwrite a known latest season's amounts
    if incoming_season:
        apply_latest = amparser.is_season_newer_or_equal(
            incoming_season, existing_latest,
        )
    else:
        apply_latest = not existing_latest and not snap

    changed_fields = []
    if apply_latest:
        tracked_fields = ["mrp", "ptr", "ex_mill_price", "bale_pack_size"]
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

    merged_extra = amparser.merge_extra_attributes(
        existing["extra_attributes"],
        article_data.get("extra_attributes") or {},
        overwrite_nonblank=apply_latest,
    )

    if apply_latest:
        conn.execute(
            """UPDATE article_master SET
               category = ?, product_type = ?, brand = ?, size = ?, mrp = ?, ptr = ?, ex_mill_price = ?,
               bale_pack_size = ?, season_tag = ?, extra_attributes = ?, source_filename = ?,
               updated_at = ?
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
                _clean_extra_json(merged_extra),
                source_filename,
                now,
                existing["id"],
                user_id,
            ),
        )
    else:
        # Older season: keep latest core prices; still refresh sticky extras + brand/size if provided.
        # Category still moves (comforter→TOB) even on older-season gap-fill.
        conn.execute(
            """UPDATE article_master SET
               category = COALESCE(?, category),
               product_type = COALESCE(?, product_type),
               brand = COALESCE(?, brand),
               size = COALESCE(?, size),
               bale_pack_size = COALESCE(?, bale_pack_size),
               extra_attributes = ?,
               updated_at = ?
               WHERE id = ? AND user_id = ?""",
            (
                article_data.get("category"),
                article_data.get("product_type"),
                article_data.get("brand"),
                article_data.get("size"),
                article_data.get("bale_pack_size"),
                _clean_extra_json(merged_extra),
                now,
                existing["id"],
                user_id,
            ),
        )

    if incoming_season:
        upsert_season_prices(
            conn, existing["id"], incoming_season, article_data,
            source_filename=source_filename, changed_at=now,
        )
    # Invariant: list amounts always = latest season snapshot
    sync_article_core_prices_to_latest_season(conn, existing["id"], persist=True)

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


def upsert_season_prices(conn, article_id, season_tag, article_data, source_filename=None, changed_at=None):
    """Insert/update one season snapshot for an article. No-op if season_tag blank."""
    tag = amparser.normalize_season_tag(season_tag)
    if not tag:
        return None
    now = changed_at or datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO article_season_prices
           (article_id, season_tag, mrp, ptr, ex_mill_price, source_filename, changed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(article_id, season_tag) DO UPDATE SET
             mrp = excluded.mrp,
             ptr = excluded.ptr,
             ex_mill_price = excluded.ex_mill_price,
             source_filename = COALESCE(excluded.source_filename, article_season_prices.source_filename),
             changed_at = excluded.changed_at
        """,
        (
            article_id,
            tag,
            article_data.get("mrp"),
            article_data.get("ptr"),
            article_data.get("ex_mill_price"),
            source_filename,
            now,
        ),
    )
    return tag


def get_season_prices_last_n(conn, article_id, user_id, limit=3):
    """
    Last N seasons that have any price, oldest→newest.
    Missing seasons are omitted (UI hides those columns).
    """
    row = conn.execute(
        "SELECT id, mrp, ptr, ex_mill_price, season_tag FROM article_master WHERE id = ? AND user_id = ?",
        (article_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError("Article not found")

    # Ensure latest article prices appear under current season_tag even if
    # season table was empty for legacy rows.
    article_id_db, cur_mrp, cur_ptr, cur_ex, cur_season = row
    cur_season = amparser.normalize_season_tag(cur_season)
    if cur_season:
        exists = conn.execute(
            "SELECT 1 FROM article_season_prices WHERE article_id = ? AND season_tag = ?",
            (article_id_db, cur_season),
        ).fetchone()
        if not exists and any(v is not None for v in (cur_mrp, cur_ptr, cur_ex)):
            upsert_season_prices(
                conn, article_id_db, cur_season,
                {"mrp": cur_mrp, "ptr": cur_ptr, "ex_mill_price": cur_ex},
            )
            conn.commit()

    rows = conn.execute(
        """SELECT season_tag, mrp, ptr, ex_mill_price, source_filename, changed_at
           FROM article_season_prices WHERE article_id = ?""",
        (article_id,),
    ).fetchall()
    snapshots = []
    for season_tag, mrp, ptr, ex_mill, source_filename, changed_at in rows:
        if mrp is None and ptr is None and ex_mill is None:
            continue
        snapshots.append({
            "season_tag": season_tag,
            "mrp": mrp,
            "ptr": ptr,
            "ex_mill_price": ex_mill,
            "source_filename": source_filename,
            "changed_at": changed_at,
            "_rank": amparser.season_rank(season_tag),
        })
    snapshots.sort(key=lambda s: (s["_rank"], s.get("changed_at") or ""))
    # Keep last N (newest), then present oldest→newest for column order
    if len(snapshots) > limit:
        snapshots = snapshots[-limit:]
    seasons = [s["season_tag"] for s in snapshots]
    rows_out = {
        "mrp": {s["season_tag"]: s["mrp"] for s in snapshots},
        "ptr": {s["season_tag"]: s["ptr"] for s in snapshots},
        "ex_mill_price": {s["season_tag"]: s["ex_mill_price"] for s in snapshots},
    }
    return {"seasons": seasons, "rows": rows_out, "snapshots": [
        {k: v for k, v in s.items() if k != "_rank"} for s in snapshots
    ]}


def delete_article(conn, user_id, article_id):
    row = conn.execute(
        "SELECT id FROM article_master WHERE id = ? AND user_id = ?",
        (article_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError("Article not found")
    conn.execute("DELETE FROM article_season_prices WHERE article_id = ?", (article_id,))
    conn.execute("DELETE FROM article_price_history WHERE article_id = ?", (article_id,))
    conn.execute("DELETE FROM article_master WHERE id = ? AND user_id = ?", (article_id, user_id))
    conn.commit()
    return True


def delete_articles_by_ids(conn, user_id, article_ids):
    """Hard-delete specific articles owned by this user. Returns deleted count."""
    ids = []
    seen = set()
    for raw in article_ids or []:
        try:
            aid = int(raw)
        except (TypeError, ValueError):
            continue
        if aid in seen:
            continue
        seen.add(aid)
        ids.append(aid)
    if not ids:
        return 0

    placeholders = ",".join("?" * len(ids))
    owned = [
        r[0]
        for r in conn.execute(
            f"SELECT id FROM article_master WHERE user_id = ? AND id IN ({placeholders})",
            (user_id, *ids),
        ).fetchall()
    ]
    if not owned:
        return 0

    owned_ph = ",".join("?" * len(owned))
    conn.execute(
        f"DELETE FROM article_season_prices WHERE article_id IN ({owned_ph})",
        owned,
    )
    conn.execute(
        f"DELETE FROM article_price_history WHERE article_id IN ({owned_ph})",
        owned,
    )
    conn.execute(
        f"DELETE FROM article_master WHERE user_id = ? AND id IN ({owned_ph})",
        (user_id, *owned),
    )
    conn.commit()
    return len(owned)


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
        f"DELETE FROM article_season_prices WHERE article_id IN ({placeholders})",
        ids,
    )
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


def purge_excluded_extra_attributes(conn) -> dict:
    """Remove excluded booking/planning keys from every article's extra_attributes JSON."""
    rows = conn.execute("SELECT id, extra_attributes FROM article_master").fetchall()
    updated = 0
    removed_keys = 0
    for article_id, raw in rows:
        try:
            extra = json.loads(raw or "{}")
        except Exception:
            extra = {}
        if not isinstance(extra, dict) or not extra:
            continue
        cleaned = amparser.strip_excluded_extra_attributes(extra)
        dropped = len(extra) - len(cleaned)
        if dropped <= 0 and cleaned == extra:
            continue
        removed_keys += dropped
        conn.execute(
            "UPDATE article_master SET extra_attributes = ?, updated_at = ? WHERE id = ?",
            (_clean_extra_json(cleaned), datetime.utcnow().isoformat(), article_id),
        )
        updated += 1
    conn.commit()
    return {"articles_updated": updated, "keys_removed": removed_keys}

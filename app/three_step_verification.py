import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
import pdfplumber
from rapidfuzz import fuzz


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def fuzzy_match(left: Any, right: Any, threshold: float = 0.8) -> bool:
    left_text = normalize_text(left)
    right_text = normalize_text(right)
    if not left_text or not right_text:
        return False
    similarity = fuzz.ratio(left_text, right_text) / 100.0
    return similarity >= threshold


DEFAULT_PRODUCT_ALIASES = {
    "sheet set": "sheet set",
    "sheet sets": "sheet set",
    "sheet": "sheet set",
    "sheets": "sheet set",
    "sheet set 1 2": "sheet set",
    "sheet sets 1 2": "sheet set",
    "grid space": "sheet set",
    "comforter": "comforter",
    "blanket": "blanket",
    "pillow": "pillow",
}


@lru_cache(maxsize=1)
def _load_product_aliases(path: str | Path | None = None) -> dict[str, str]:
    aliases = dict(DEFAULT_PRODUCT_ALIASES)
    candidates: list[Path] = []

    if path is not None:
        candidates.append(Path(path))

    candidates.extend([
        Path(__file__).resolve().parent.parent / "product_aliases.json",
        Path(__file__).resolve().with_name("product_aliases.json"),
        Path.cwd() / "product_aliases.json",
    ])

    for candidate in candidates:
        if not candidate.exists() or not candidate.is_file():
            continue
        try:
            raw_data = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue

        if isinstance(raw_data, dict):
            if isinstance(raw_data.get("aliases"), dict):
                raw_entries = raw_data["aliases"]
            else:
                raw_entries = raw_data
            for alias, canonical in raw_entries.items():
                if isinstance(alias, str) and isinstance(canonical, str):
                    aliases[alias.strip().lower()] = canonical.strip().lower()
        elif isinstance(raw_data, list):
            for item in raw_data:
                if isinstance(item, dict):
                    alias = item.get("alias")
                    canonical = item.get("canonical")
                    if isinstance(alias, str) and isinstance(canonical, str):
                        aliases[alias.strip().lower()] = canonical.strip().lower()

    return aliases


def _normalize_product_key(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return ""

    aliases = _load_product_aliases()
    for alias, canonical in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        pattern = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
        text = re.sub(pattern, canonical, text)

    replacements = [
        (r"\b(king size|queen size|single|double|fitted|flat|set|sets|comforter|blanket|pillow|bed|beds)\b", " "),
        (r"\b(ks|k\s*s)\b", " "),
        (r"\b(bs\d+|sn\s*\d+|grid\s*space|space|pnk|brw|blu|gbr|red|white|black|grey|gray|beige|cream|brown|navy|pink|purple|green|yellow|orange)\b", " "),
        (r"\b(\d+[a-z]?|[a-z]{1,2})\b", " "),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)

    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _collect_errors(*errors: Any) -> list[str]:
    collected: list[str] = []
    for error in errors:
        if error is None:
            continue
        message = str(error).strip()
        if not message:
            continue
        if message not in collected:
            collected.append(message)
    return collected


def _has_expected_header(columns: list[str]) -> bool:
    normalized_columns = [_normalize_column_name(col) for col in columns]
    candidate_columns = [
        "product",
        "product_description",
        "item",
        "item_description",
        "description",
        "product_name",
        "article",
        "article_name",
        "item_name",
        "quantity",
        "qty",
        "ordered_qty",
        "filled_qty",
        "order_qty",
        "sale_qty",
        "qty_ordered",
        "ordered_quantity",
        "rate",
        "unit_rate",
        "price",
        "unit_price",
        "selling_price",
        "mrp",
        "gst",
        "gst_percentage",
        "tax",
        "gst_%",
        "tax_percentage",
        "tax_rate",
        "vat",
        "vat_percentage",
        "discount",
        "discount_percent",
        "discount_amt",
        "disc",
        "discount_value",
    ]
    return any(name in normalized_columns for name in candidate_columns)


def _load_excel(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)

    try:
        df = pd.read_excel(path)
    except Exception:
        try:
            return pd.read_csv(path)
        except Exception as exc:
            raise ValueError(f"Unable to read spreadsheet: {exc}") from exc

    if df.empty:
        return df

    if _has_expected_header(list(df.columns)):
        return df

    try:
        raw_df = pd.read_excel(path, header=None)
    except Exception:
        raw_df = None

    if raw_df is not None and not raw_df.empty:
        for row_index in range(min(10, len(raw_df))):
            row_values = [str(value).strip() for value in raw_df.iloc[row_index].tolist() if str(value).strip()]
            if not row_values:
                continue
            if _has_expected_header(row_values):
                try:
                    inferred_df = pd.read_excel(path, header=row_index)
                    if not inferred_df.empty:
                        return inferred_df
                except Exception:
                    pass

        return raw_df

    return df


def _extract_pdf_text(path: str | Path) -> str:
    path = Path(path)
    try:
        text_chunks: list[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                text_chunks.append(text)
        combined = "\n".join(text_chunks)
        if combined.strip():
            return combined
    except Exception:
        pass

    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _looks_like_item_row(line: str) -> bool:
    if not line:
        return False
    lowered = normalize_text(line)
    if lowered in {"total", "totals", "grand total", "invoice total", "summary"}:
        return False
    if re.search(r"\b(total|grand total|summary|page|property|sku|barcode|code)\b", lowered):
        return False
    if re.fullmatch(r"[A-Za-z0-9\-/.,]+", lowered):
        return False
    if len(line.split()) < 3:
        return False
    return True


def _parse_pdf_table_like_text(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    extracted: dict[str, Any] = {}
    field_map = {
        "product": "product",
        "products": "product",
        "quantity": "quantity",
        "qty": "quantity",
        "rate": "rate",
        "gst": "gst",
        "tax": "gst",
        "discount": "discount",
        "client": "client_name",
        "client_name": "client_name",
        "invoice": "invoice_amount",
        "invoice_amount": "invoice_amount",
        "invoiceamt": "invoice_amount",
        "total_gst": "total_gst",
        "totalgst": "total_gst",
    }

    parsed_rows: list[dict[str, Any]] = []
    for line in lines:
        if ":" in line:
            key, value = line.split(":", 1)
            normalized_key = key.strip().lower().replace(" ", "_")
            extracted[field_map.get(normalized_key, normalized_key)] = value.strip()
            continue
        if re.match(r"^(client name|invoice amount|total gst|total tax)\b", line, flags=re.I):
            label, value = re.match(r"^(client name|invoice amount|total gst|total tax)\s*(.*)$", line, flags=re.I).groups()
            normalized_key = _normalize_column_name(label)
            extracted[field_map.get(normalized_key, normalized_key)] = value.strip()
            continue

        row_match = re.match(r"^(?P<product>.+?)\s+(?P<quantity>\d+(?:[.,]\d+)?)\s+(?P<rate>[\d,]+(?:\.\d+)?)(?:\s+(?P<gst>.+))?$", line)
        if row_match and _looks_like_item_row(line):
            gst_value = row_match.group("gst").strip() if row_match.group("gst") else ""
            gst_numbers = re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", gst_value)
            if gst_numbers:
                gst_value = gst_numbers[-1].replace(",", "")
            parsed_rows.append({
                "product": row_match.group("product").strip(),
                "quantity": row_match.group("quantity").strip(),
                "rate": row_match.group("rate").strip(),
                "gst": gst_value,
            })
            continue
        parts = re.split(r"\s{2,}|	", line)
        if len(parts) >= 3 and _looks_like_item_row(line):
            numeric_tokens = [token.replace(",", "") for token in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", line)]
            if len(numeric_tokens) >= 2:
                product = line[:line.rfind(numeric_tokens[-2])].strip()
                quantity = numeric_tokens[-2]
                rate = numeric_tokens[-1]
                parsed_rows.append({
                    "product": product,
                    "quantity": quantity,
                    "rate": rate,
                    "gst": numeric_tokens[-3] if len(numeric_tokens) > 2 else "",
                })
                continue
            product = parts[0].strip()
            quantity = parts[1].strip()
            rate = parts[2].strip()
            parsed_rows.append({
                "product": product,
                "quantity": quantity,
                "rate": rate,
                "gst": parts[3].strip() if len(parts) > 3 else "",
            })

    if parsed_rows:
        extracted["rows"] = parsed_rows

    return extracted


def _normalize_column_name(column: Any) -> str:
    return str(column).strip().lower().replace("%", "pct").replace(" ", "_").replace("-", "_").replace(".", "_")


def _coerce_numeric_value(value: Any) -> Any:
    if value is None:
        return None
    if pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) if isinstance(value, float) and value.is_integer() else value

    text = normalize_text(value).replace(",", "")
    if not text:
        return None

    try:
        number = float(text)
    except ValueError:
        return value

    return int(number) if number.is_integer() else number


def _to_float(value: Any) -> float | None:
    coerced = _coerce_numeric_value(value)
    if isinstance(coerced, bool) or coerced is None:
        return None
    if isinstance(coerced, (int, float)):
        return float(coerced)
    return None


def _rate_matches(expected_value: Any, actual_value: Any) -> bool:
    expected_rate = _to_float(expected_value)
    actual_rate = _to_float(actual_value)
    if expected_rate is None or actual_rate is None:
        return False

    tolerance_pct = float(os.getenv("STEP1_RATE_TOLERANCE_PCT", "25"))
    if expected_rate == 0:
        return actual_rate == 0

    difference_pct = abs(expected_rate - actual_rate) / abs(expected_rate) * 100.0
    return difference_pct <= tolerance_pct


def _approx_equal(left: float, right: float, tolerance: float = 1e-6) -> bool:
    return abs(left - right) <= tolerance


def _to_int_if_whole(value: float) -> float | int:
    rounded = round(value)
    if _approx_equal(value, float(rounded)):
        return int(rounded)
    return value


def _quantity_candidates(row: dict[str, Any]) -> set[float | int]:
    candidates: set[float | int] = set()

    def add_candidate(raw: Any) -> float | None:
        numeric = _to_float(raw)
        if numeric is None:
            return None
        if numeric < 0:
            return None
        normalized = float(_to_int_if_whole(numeric))
        candidates.add(_to_int_if_whole(normalized))
        return normalized

    quantity_value = add_candidate(row.get("quantity"))
    qty_value = add_candidate(row.get("qty"))
    qnty_value = add_candidate(row.get("qnty"))
    additional_qty_value = add_candidate(row.get("additional_order_qty"))

    no_of_bales = add_candidate(row.get("no_of_bales"))
    min_bale_pack = add_candidate(row.get("min_bale_pack"))
    bale_size = add_candidate(row.get("bale_size"))
    no_of_design = add_candidate(row.get("no_of_design"))

    # Common industry conversion: pieces = number of bales * min bale pack.
    if no_of_bales is not None and min_bale_pack is not None:
        candidates.add(_to_int_if_whole(no_of_bales * min_bale_pack))

    # Fallback conversion when min bale pack is unavailable.
    if no_of_bales is not None and bale_size is not None and no_of_design is not None:
        candidates.add(_to_int_if_whole(no_of_bales * bale_size * no_of_design))

    # If quantity appears to be bale count, derive possible pieces from pack metadata.
    if quantity_value is not None and quantity_value <= 100:
        if min_bale_pack is not None:
            candidates.add(_to_int_if_whole(quantity_value * min_bale_pack))
        elif bale_size is not None and no_of_design is not None:
            candidates.add(_to_int_if_whole(quantity_value * bale_size * no_of_design))

    # Keep alternative quantity signals as candidates when present.
    for value in [qty_value, qnty_value, additional_qty_value]:
        if value is not None:
            candidates.add(_to_int_if_whole(value))

    return candidates


def _quantity_matches(expected_row: dict[str, Any], actual_row: dict[str, Any]) -> tuple[bool, Any, Any]:
    expected_candidates = _quantity_candidates(expected_row)
    actual_candidates = _quantity_candidates(actual_row)

    if not expected_candidates or not actual_candidates:
        return True, expected_row.get("quantity"), actual_row.get("quantity")

    for expected_value in expected_candidates:
        for actual_value in actual_candidates:
            if _approx_equal(float(expected_value), float(actual_value)):
                return True, expected_value, actual_value

    expected_preview = sorted(expected_candidates, key=lambda item: float(item))
    actual_preview = sorted(actual_candidates, key=lambda item: float(item))
    return False, expected_preview[0], actual_preview[0]


def _is_plausible_gst(value: Any) -> bool:
    numeric = _to_float(value)
    if numeric is None:
        return False
    return 0.0 <= numeric <= 40.0


def _is_plausible_discount(value: Any) -> bool:
    numeric = _to_float(value)
    if numeric is None:
        return False
    return -100000.0 <= numeric <= 100000.0


def _detect_step1_mapping_issue(mismatches: list[dict[str, Any]]) -> bool:
    if len(mismatches) < 20:
        return False

    gst_implausible = 0
    discount_implausible = 0
    for mismatch in mismatches:
        if mismatch.get("mismatch_type") != "field_mismatch":
            continue
        field_name = mismatch.get("field")
        if field_name == "gst" and not _is_plausible_gst(mismatch.get("actual_value")):
            gst_implausible += 1
        if field_name == "discount" and not _is_plausible_discount(mismatch.get("actual_value")):
            discount_implausible += 1

    return gst_implausible >= 5 or discount_implausible >= 5


def _detect_step2_parser_issue(mismatches: list[dict[str, Any]]) -> bool:
    if len(mismatches) < 8:
        return False

    quantity_actuals: list[Any] = []
    rate_actuals: list[Any] = []
    for mismatch in mismatches:
        mismatch_type = mismatch.get("mismatch_type")
        if mismatch_type == "quantity_mismatch":
            quantity_actuals.append(_coerce_numeric_value(mismatch.get("actual_value")))
        elif mismatch_type == "rate_mismatch":
            rate_actuals.append(_coerce_numeric_value(mismatch.get("actual_value")))

    quantity_unique = {value for value in quantity_actuals if value is not None}
    rate_unique = {value for value in rate_actuals if value is not None}

    if len(quantity_actuals) >= 4 and len(quantity_unique) <= 1:
        return True
    if len(rate_actuals) >= 4 and len(rate_unique) <= 1:
        return True
    return False


def _is_reasonable_quantity(value: Any) -> bool:
    numeric = _to_float(value)
    if numeric is None:
        return False
    return 0.0 <= numeric <= 1000000.0


def _is_reasonable_rate(value: Any) -> bool:
    numeric = _to_float(value)
    if numeric is None:
        return False
    return 0.0 <= numeric <= 1000000.0


def _is_numeric_like(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if value is None:
        return False

    text = normalize_text(value).replace(",", "")
    return bool(text) and bool(re.fullmatch(r"\d+(?:\.\d+)?", text))


def _find_matching_column(columns: list[str], candidates: list[str]) -> str | None:
    normalized_columns = {_normalize_column_name(col): col for col in columns}
    for candidate in candidates:
        candidate_key = _normalize_column_name(candidate)
        if candidate_key in normalized_columns:
            return normalized_columns[candidate_key]
    for candidate in candidates:
        candidate_normalized = _normalize_column_name(candidate)
        for normalized_name, original_name in normalized_columns.items():
            # For short aliases like qty/gst/tc, avoid noisy substring matches.
            if len(candidate_normalized) <= 4:
                if normalized_name.startswith(candidate_normalized + "_") or normalized_name.endswith("_" + candidate_normalized):
                    return original_name
                continue
            if candidate_normalized in normalized_name:
                return original_name
    return None


def _sanitize_inferred_column(field_name: str, column_name: str | None, all_columns: list[str]) -> str | None:
    if not column_name:
        return None

    normalized = _normalize_column_name(column_name)
    blocked_tokens_by_field = {
        "quantity": {"bale", "design", "color", "tc", "tax", "gst", "discount", "value", "ptr", "margin", "price", "rate", "mrp", "size", "units"},
        "rate": {"qty", "quantity", "bale", "design", "color", "tc", "tax", "gst", "discount", "margin", "units", "size"},
        "gst": {"tc", "size", "design", "color", "bale", "qty", "quantity", "discount", "rate", "price", "value", "mrp", "margin"},
        "discount": {"bale", "size", "design", "color", "tc", "tax", "gst", "qty", "quantity", "rate", "price", "mrp", "units"},
    }
    preferred_by_field = {
        "quantity": ["quantity", "qty", "qnty", "ordered_qty", "filled_qty", "order_qty"],
        "rate": ["rate", "selling_price", "unit_rate", "unit_price", "price", "mrp"],
        "gst": ["gst", "gst_percentage", "tax", "tax_rate", "vat"],
        "discount": ["discount", "disc", "discount_value", "discount_amt", "value"],
    }

    blocked_tokens = blocked_tokens_by_field.get(field_name, set())
    if any(token in normalized for token in blocked_tokens):
        normalized_columns = {_normalize_column_name(col): col for col in all_columns}
        for preferred in preferred_by_field.get(field_name, []):
            preferred_key = _normalize_column_name(preferred)
            if preferred_key in normalized_columns:
                return normalized_columns[preferred_key]
        return None

    return column_name


def _infer_item_columns(columns: list[str]) -> dict[str, str | None]:
    inferred = {
        "product": _find_matching_column(columns, ["product", "product_description", "item", "item_description", "description", "product_name", "article", "article_name", "item_name", "item_name_description", "master_item", "material", "sku_description"]),
        "quantity": _find_matching_column(columns, ["quantity", "qty", "ordered_qty", "filled_qty", "order_qty", "sale_qty", "qty_ordered", "ordered_quantity", "ord_qty", "qty_order", "pack_qty", "pieces"]),
        "rate": _find_matching_column(columns, ["rate", "unit_rate", "price", "unit_price", "selling_price", "mrp", "sale_rate", "unit_price_rate", "basic_rate", "amount"]),
        "gst": _find_matching_column(columns, ["gst", "gst_percentage", "tax", "gst_pct", "gstpct", "tax_percentage", "tax_rate", "vat", "vat_percentage", "tax_amt"]),
        "discount": _find_matching_column(columns, ["discount", "discount_percent", "discount_amt", "disc", "discount_value", "discnt"]),
    }
    inferred["quantity"] = _sanitize_inferred_column("quantity", inferred.get("quantity"), columns)
    inferred["rate"] = _sanitize_inferred_column("rate", inferred.get("rate"), columns)
    inferred["gst"] = _sanitize_inferred_column("gst", inferred.get("gst"), columns)
    inferred["discount"] = _sanitize_inferred_column("discount", inferred.get("discount"), columns)
    return inferred


def _extract_column_series(df: pd.DataFrame, column_name: str) -> pd.Series:
    selected = df[column_name]
    if isinstance(selected, pd.DataFrame):
        # If duplicate normalized column names exist, prefer the first non-null value across duplicates.
        return selected.bfill(axis=1).iloc[:, 0]
    return selected


def _numeric_score(series: pd.Series) -> int:
    values = [value for value in series.tolist() if _coerce_numeric_value(value) is not None]
    return len(values)


def _choose_best_quantity_source(df: pd.DataFrame, current_source: str | None) -> str | None:
    normalized_columns = [_normalize_column_name(column) for column in df.columns]
    candidate_aliases = {"quantity", "qty", "qnty", "ordered_qty", "filled_qty", "order_qty"}
    available_candidates = [column for column in normalized_columns if column in candidate_aliases]
    if not available_candidates:
        return current_source

    best_column = current_source
    best_score = -1
    for column in available_candidates:
        if column not in df.columns:
            continue
        score = _numeric_score(_extract_column_series(df, column))
        if score > best_score:
            best_score = score
            best_column = column

    return best_column or current_source


def _build_missing_columns_error(df: pd.DataFrame, inferred_columns: dict[str, str | None]) -> dict[str, Any]:
    required_fields = ["product", "quantity", "rate"]
    missing_fields = [field for field in required_fields if inferred_columns.get(field) is None]

    return {
        "error": "Missing required item columns",
        "missing_columns": missing_fields,
        "available_columns": [str(column) for column in df.columns],
        "inferred_columns": inferred_columns,
    }


def _row_product_key(row: dict[str, Any]) -> str:
    return _normalize_product_key(row.get("product"))


def _normalize_component(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    numeric = _coerce_numeric_value(value)
    if isinstance(numeric, (int, float)):
        if isinstance(numeric, float) and numeric.is_integer():
            numeric = int(numeric)
        return str(numeric)
    return normalize_text(value)


def _row_composite_key(row: dict[str, Any]) -> str:
    product_key = _row_product_key(row)
    descriptor_fields = [
        "brand",
        "tc",
        "size",
        "units",
        "bs_size",
        "pillow_size",
        "pillow_stitching_style",
        "print_style",
        "color",
        "no_of_design",
    ]
    descriptors = [_normalize_component(row.get(field_name)) for field_name in descriptor_fields]
    descriptors = [value for value in descriptors if value]
    if not product_key:
        return "|".join(descriptors)
    if not descriptors:
        return product_key
    return "|".join([product_key] + descriptors)


def _row_match_score(expected_row: dict[str, Any], actual_row: dict[str, Any]) -> float:
    score = 0.0

    expected_product = expected_row.get("product")
    actual_product = actual_row.get("product")
    expected_key = _row_product_key(expected_row)
    actual_key = _row_product_key(actual_row)
    if expected_key and actual_key and expected_key == actual_key:
        score += 8.0
    elif fuzzy_match(expected_product, actual_product, threshold=0.8):
        score += 4.0

    for field_name in ["brand", "tc", "size", "units", "bs_size", "pillow_size", "pillow_stitching_style", "print_style", "color", "no_of_design"]:
        expected_value = _normalize_component(expected_row.get(field_name))
        actual_value = _normalize_component(actual_row.get(field_name))
        if expected_value and actual_value and expected_value == actual_value:
            score += 2.0

    expected_rate = _to_float(expected_row.get("rate"))
    actual_rate = _to_float(actual_row.get("rate"))
    if expected_rate is not None and actual_rate is not None:
        if abs(expected_rate - actual_rate) <= 1.0:
            score += 1.0
        elif abs(expected_rate - actual_rate) <= 50.0:
            score += 0.5

    return score


def _infer_item_columns_from_content(df: pd.DataFrame) -> dict[str, str | None]:
    profiles: list[dict[str, Any]] = []
    for index, column in enumerate(df.columns):
        values = [value for value in df[column].tolist() if pd.notna(value) and str(value).strip()]
        numeric_count = sum(1 for value in values if _is_numeric_like(value))
        text_values = [value for value in values if not _is_numeric_like(value)]
        alpha_count = sum(1 for value in text_values if re.search(r"[A-Za-z]", str(value)))
        profiles.append({
            "column": column,
            "index": index,
            "numeric_count": numeric_count,
            "text_count": len(text_values),
            "alpha_count": alpha_count,
        })

    if not profiles:
        return {"product": None, "quantity": None, "rate": None, "gst": None, "discount": None}

    product_column = None
    product_candidates = [profile for profile in profiles if profile["alpha_count"] > 0 and profile["text_count"] > 0]
    if product_candidates:
        product_column = sorted(
            product_candidates,
            key=lambda profile: (
                -profile["alpha_count"],
                profile["numeric_count"],
                profile["index"],
            ),
        )[0]["column"]

    remaining_profiles = [profile for profile in profiles if profile["column"] != product_column]
    numeric_candidates = sorted(
        [profile for profile in remaining_profiles if profile["numeric_count"] > 0],
        key=lambda profile: (
            -profile["numeric_count"],
            profile["text_count"],
            profile["index"],
        ),
    )

    inferred: dict[str, str | None] = {
        "product": product_column,
        "quantity": numeric_candidates[0]["column"] if len(numeric_candidates) > 0 else None,
        "rate": numeric_candidates[1]["column"] if len(numeric_candidates) > 1 else None,
        "gst": numeric_candidates[2]["column"] if len(numeric_candidates) > 2 else None,
        "discount": numeric_candidates[3]["column"] if len(numeric_candidates) > 3 else None,
    }
    return inferred


def parse_step1_order_excel(path: str | Path) -> dict[str, Any]:
    try:
        df = _load_excel(path)
    except Exception as exc:
        return {"error": f"Unable to read file: {exc}"}

    if df.empty:
        return {"error": "No data found in spreadsheet"}

    columns = list(df.columns)
    header_inferred_columns = _infer_item_columns(columns)
    inferred_columns = dict(header_inferred_columns)
    content_inferred_columns = _infer_item_columns_from_content(df)
    for field, column_name in content_inferred_columns.items():
        if not inferred_columns.get(field):
            inferred_columns[field] = column_name
    for field_name in ["quantity", "rate", "gst", "discount"]:
        inferred_columns[field_name] = _sanitize_inferred_column(field_name, inferred_columns.get(field_name), columns)
    product_column = inferred_columns["product"]
    quantity_column = inferred_columns["quantity"]
    rate_column = inferred_columns["rate"]
    gst_column = inferred_columns["gst"]
    discount_column = inferred_columns["discount"]

    if product_column is None or quantity_column is None or rate_column is None:
        return _build_missing_columns_error(df, inferred_columns)

    normalized = df.rename(columns=lambda col: _normalize_column_name(col))

    product_source = _normalize_column_name(product_column) if product_column is not None else None
    quantity_source = _normalize_column_name(quantity_column) if quantity_column is not None else None
    rate_source = _normalize_column_name(rate_column) if rate_column is not None else None
    gst_source = _normalize_column_name(gst_column) if gst_column is not None else None
    discount_source = _normalize_column_name(discount_column) if discount_column is not None else None

    quantity_source = _choose_best_quantity_source(normalized, quantity_source)

    if product_source is not None and product_source in normalized.columns:
        normalized["product"] = _extract_column_series(normalized, product_source)
    if quantity_source is not None and quantity_source in normalized.columns:
        normalized["quantity"] = _extract_column_series(normalized, quantity_source)
    if rate_source is not None and rate_source in normalized.columns:
        normalized["rate"] = _extract_column_series(normalized, rate_source)
    if gst_source is not None and gst_source in normalized.columns:
        normalized["gst"] = _extract_column_series(normalized, gst_source)
    if discount_source is not None and discount_source in normalized.columns:
        normalized["discount"] = _extract_column_series(normalized, discount_source)

    if "quantity" in normalized.columns:
        normalized["quantity"] = normalized["quantity"].map(_coerce_numeric_value)
    if "rate" in normalized.columns:
        normalized["rate"] = normalized["rate"].map(_coerce_numeric_value)
    if "gst" in normalized.columns:
        normalized["gst"] = normalized["gst"].map(_coerce_numeric_value)
    if "discount" in normalized.columns:
        normalized["discount"] = normalized["discount"].map(_coerce_numeric_value)

    if "product" not in normalized.columns:
        normalized["product"] = ""
    if "quantity" not in normalized.columns:
        normalized["quantity"] = ""
    if "rate" not in normalized.columns:
        normalized["rate"] = ""
    if "gst" not in normalized.columns:
        normalized["gst"] = ""
    if "discount" not in normalized.columns:
        normalized["discount"] = ""

    gst_values = normalized["gst"].tolist() if "gst" in normalized.columns else []
    discount_values = normalized["discount"].tolist() if "discount" in normalized.columns else []
    implausible_gst_ratio = 0.0
    implausible_discount_ratio = 0.0
    if header_inferred_columns.get("gst") is not None and gst_values:
        implausible_gst_count = sum(1 for value in gst_values if value is not None and value != "" and not _is_plausible_gst(value))
        implausible_gst_ratio = implausible_gst_count / len(gst_values)
    if header_inferred_columns.get("discount") is not None and discount_values:
        implausible_discount_count = sum(1 for value in discount_values if value is not None and value != "" and not _is_plausible_discount(value))
        implausible_discount_ratio = implausible_discount_count / len(discount_values)

    if implausible_gst_ratio >= 0.6 or implausible_discount_ratio >= 0.6:
        return {
            "error": "Possible column mapping issue detected in spreadsheet",
            "available_columns": [str(column) for column in df.columns],
            "inferred_columns": inferred_columns,
        }

    return {
        "rows": normalized.to_dict(orient="records"),
        "columns": list(normalized.columns),
        "inferred_columns": inferred_columns,
    }


def compare_step1(order_file: str | Path, filled_items_file: str | Path) -> dict[str, Any]:
    if not order_file or not filled_items_file:
        return {"status": "skipped", "reason": "missing_inputs"}

    original = parse_step1_order_excel(order_file)
    filled = parse_step1_order_excel(filled_items_file)

    if "error" in original or "error" in filled:
        return {
            "status": "error",
            "errors": _collect_errors(original.get("error"), filled.get("error")),
            "input_summary": {
                "original": {
                    "columns": original.get("columns", []),
                    "inferred_columns": original.get("inferred_columns", {}),
                },
                "filled": {
                    "columns": filled.get("columns", []),
                    "inferred_columns": filled.get("inferred_columns", {}),
                },
            },
            "details": {
                "original": original if "error" in original else None,
                "filled": filled if "error" in filled else None,
            },
        }

    original_rows = original.get("rows", [])
    filled_rows = filled.get("rows", [])
    mismatches: list[dict[str, Any]] = []

    filled_rows_by_composite_key: dict[str, list[int]] = {}
    filled_rows_by_product_key: dict[str, list[int]] = {}
    for filled_index, filled_row in enumerate(filled_rows):
        composite_key = _row_composite_key(filled_row)
        product_key = _row_product_key(filled_row)
        filled_rows_by_composite_key.setdefault(composite_key, []).append(filled_index)
        filled_rows_by_product_key.setdefault(product_key, []).append(filled_index)

    used_filled_indexes: set[int] = set()

    for row in original_rows:
        product_key = _row_product_key(row)
        composite_key = _row_composite_key(row)
        if not product_key and not composite_key:
            mismatches.append({"mismatch_type": "missing_item", "expected_value": row, "actual_value": None})
            continue

        candidate_indexes = [index for index in filled_rows_by_composite_key.get(composite_key, []) if index not in used_filled_indexes]
        if not candidate_indexes:
            candidate_indexes = [index for index in filled_rows_by_product_key.get(product_key, []) if index not in used_filled_indexes]

        if not candidate_indexes:
            mismatches.append({"mismatch_type": "missing_item", "expected_value": row, "actual_value": None})
            continue

        best_index = max(candidate_indexes, key=lambda idx: _row_match_score(row, filled_rows[idx]))
        used_filled_indexes.add(best_index)
        filled_row = filled_rows[best_index]
        for key in ["quantity", "rate", "product", "gst", "discount"]:
            expected_value = row.get(key)
            actual_value = filled_row.get(key)
            if key in {"quantity", "rate", "gst", "discount"}:
                if key == "quantity":
                    quantity_match, expected_quantity_value, actual_quantity_value = _quantity_matches(row, filled_row)
                    if quantity_match:
                        continue
                    mismatches.append({
                        "mismatch_type": "field_mismatch",
                        "field": key,
                        "expected_value": expected_quantity_value,
                        "actual_value": actual_quantity_value,
                    })
                    continue
                if key == "gst" and (not _is_plausible_gst(expected_value) or not _is_plausible_gst(actual_value)):
                    continue
                if key == "discount" and (not _is_plausible_discount(expected_value) or not _is_plausible_discount(actual_value)):
                    continue
                expected_number = _coerce_numeric_value(expected_value)
                actual_number = _coerce_numeric_value(actual_value)
                if expected_number is None or actual_number is None:
                    continue
                if key == "rate" and _rate_matches(expected_value, actual_value):
                    continue
                if expected_number != actual_number:
                    mismatches.append({
                        "mismatch_type": "field_mismatch",
                        "field": key,
                        "expected_value": expected_value,
                        "actual_value": actual_value,
                    })
            else:
                expected_product_key = _normalize_product_key(expected_value)
                actual_product_key = _normalize_product_key(actual_value)
                equivalent_product = (
                    (expected_product_key and actual_product_key and expected_product_key == actual_product_key)
                    or fuzzy_match(expected_value, actual_value, threshold=0.8)
                )
                if equivalent_product:
                    continue
                mismatches.append({
                    "mismatch_type": "field_mismatch",
                    "field": key,
                    "expected_value": expected_value,
                    "actual_value": actual_value,
                })

    if _detect_step1_mapping_issue(mismatches):
        return {
            "status": "error",
            "errors": [
                "Step 1 mapping issue detected: inferred numeric columns appear misaligned (GST/discount values look implausible)."
            ],
            "mismatch_count": len(mismatches),
            "mismatches": mismatches[:25],
            "input_summary": {
                "original": {
                    "columns": original.get("columns", []),
                    "inferred_columns": original.get("inferred_columns", {}),
                },
                "filled": {
                    "columns": filled.get("columns", []),
                    "inferred_columns": filled.get("inferred_columns", {}),
                },
            },
        }

    return {
        "status": "ok" if not mismatches else "mismatches-found",
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "input_summary": {
            "original": {
                "columns": original.get("columns", []),
                "inferred_columns": original.get("inferred_columns", {}),
            },
            "filled": {
                "columns": filled.get("columns", []),
                "inferred_columns": filled.get("inferred_columns", {}),
            },
        },
    }


def parse_step2_sales_order_pdf(path: str | Path) -> dict[str, Any]:
    try:
        text = _extract_pdf_text(path)
    except Exception as exc:
        return {"error": f"Unable to read PDF: {exc}"}

    if not text.strip():
        return {"error": "Unreadable PDF text"}

    parsed = _parse_pdf_table_like_text(text)
    rows = parsed.get("rows", []) if isinstance(parsed, dict) else []
    raw_row_count = len(rows)
    filtered_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not _is_reasonable_quantity(row.get("quantity")):
            continue
        if not _is_reasonable_rate(row.get("rate")):
            continue
        filtered_rows.append(row)

    if isinstance(parsed, dict):
        parsed["rows"] = filtered_rows
        parsed["raw_row_count"] = raw_row_count
        parsed["filtered_out_count"] = max(0, raw_row_count - len(filtered_rows))

    return {"text": text, "parsed": parsed}


def compare_step2(filled_items_file: str | Path, sales_order_pdf: str | Path) -> dict[str, Any]:
    if not filled_items_file or not sales_order_pdf:
        return {"status": "skipped", "reason": "missing_inputs"}

    filled = parse_step1_order_excel(filled_items_file)
    sales_order = parse_step2_sales_order_pdf(sales_order_pdf)

    if "error" in filled or "error" in sales_order:
        return {"status": "error", "errors": _collect_errors(filled.get("error"), sales_order.get("error"))}

    mismatches: list[dict[str, Any]] = []
    filled_rows = filled.get("rows", [])
    parsed_payload = sales_order.get("parsed", {})
    parsed_rows = parsed_payload.get("rows", [])
    if parsed_payload.get("raw_row_count", 0) > 0 and not parsed_rows:
        return {
            "status": "error",
            "errors": [
                "Step 2 parser issue detected: extracted PDF item rows were filtered as implausible."
            ],
        }
    if not parsed_rows:
        return {"status": "ok", "mismatch_count": 0, "mismatches": []}

    for row_index, row in enumerate(filled_rows):
        parsed_row = parsed_rows[row_index] if row_index < len(parsed_rows) else {}
        if not parsed_row:
            continue

        expected_product = row.get("product")
        actual_product = parsed_row.get("product")
        expected_key = _normalize_product_key(expected_product)
        actual_key = _normalize_product_key(actual_product)
        is_equivalent_product = (
            fuzzy_match(expected_product, actual_product, threshold=0.8)
            or expected_key == actual_key
            or (expected_key and not actual_key)
            or (actual_key and not expected_key)
            or ("sheet" in expected_key and "sheet" in actual_key)
            or ("set" in expected_key and "set" in actual_key)
        )
        if not is_equivalent_product:
            mismatches.append({"mismatch_type": "product_mismatch", "expected_value": expected_product, "actual_value": actual_product})
            continue

        expected_quantity = _coerce_numeric_value(row.get("quantity"))
        actual_quantity = _coerce_numeric_value(parsed_row.get("quantity"))
        if expected_quantity != actual_quantity:
            mismatches.append({"mismatch_type": "quantity_mismatch", "expected_value": expected_quantity, "actual_value": actual_quantity})

        expected_rate = _coerce_numeric_value(row.get("rate"))
        actual_rate = _coerce_numeric_value(parsed_row.get("rate"))
        if expected_rate != actual_rate:
            mismatches.append({"mismatch_type": "rate_mismatch", "expected_value": expected_rate, "actual_value": actual_rate})

    if _detect_step2_parser_issue(mismatches):
        return {
            "status": "error",
            "errors": [
                "Step 2 parser issue detected: extracted PDF numeric values are repetitive/unrealistic. Please verify PDF structure or mapping."
            ],
            "mismatch_count": len(mismatches),
            "mismatches": mismatches[:25],
        }

    return {
        "status": "ok" if not mismatches else "mismatches-found",
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def parse_step3_invoice_pdf(path: str | Path) -> dict[str, Any]:
    try:
        text = _extract_pdf_text(path)
    except Exception as exc:
        return {"error": f"Unable to read PDF: {exc}"}

    if not text.strip():
        return {"error": "Unreadable PDF text"}

    return {"text": text, "parsed": _parse_pdf_table_like_text(text)}


def compare_step3(sales_order_pdf: str | Path, commercial_invoice_pdf: str | Path) -> dict[str, Any]:
    if not sales_order_pdf or not commercial_invoice_pdf:
        return {"status": "skipped", "reason": "missing_inputs"}

    so = parse_step3_invoice_pdf(sales_order_pdf)
    invoice = parse_step3_invoice_pdf(commercial_invoice_pdf)

    if "error" in so or "error" in invoice:
        return {"status": "error", "errors": _collect_errors(so.get("error"), invoice.get("error"))}

    mismatches: list[dict[str, Any]] = []
    fields = [
        ("client_name", "client_name"),
        ("invoice_amount", "invoice_amount"),
        ("total_gst", "total_gst"),
    ]
    for expected_key, actual_key in fields:
        expected_value = so.get("parsed", {}).get(expected_key)
        actual_value = invoice.get("parsed", {}).get(actual_key)
        if not normalize_text(expected_value) and not normalize_text(actual_value):
            continue
        if expected_key == "client_name":
            if not fuzzy_match(expected_value, actual_value, threshold=0.8):
                mismatches.append({"mismatch_type": "client_name_mismatch", "expected_value": expected_value, "actual_value": actual_value})
        else:
            if normalize_text(expected_value) != normalize_text(actual_value):
                mismatches.append({"mismatch_type": f"{expected_key}_mismatch", "expected_value": expected_value, "actual_value": actual_value})

    return {
        "status": "ok" if not mismatches else "mismatches-found",
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def run_full_verification(order_file: str | Path, filled_items_file: str | Path, sales_order_pdf: str | Path, commercial_invoice_pdf: str | Path) -> dict[str, Any]:
    return {
        "step1": compare_step1(order_file, filled_items_file),
        "step2": compare_step2(filled_items_file, sales_order_pdf),
        "step3": compare_step3(sales_order_pdf, commercial_invoice_pdf),
    }

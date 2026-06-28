from typing import Any

from app.three_step_verification import (
    _extract_pdf_text,
    _parse_pdf_table_like_text,
    compare_step1,
    compare_step2,
    compare_step3,
    run_full_verification,
)


def parse_distributor_fields_from_text(text: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip().lower().replace(" ", "_")
        cleaned_value = value.strip()
        if not cleaned_value:
            continue
        if normalized_key in {"distributor_name", "name", "distributor"}:
            parsed["name"] = cleaned_value
        elif normalized_key in {"distributor_code", "dist_code", "code"}:
            parsed["distributor_code"] = cleaned_value
        elif normalized_key in {"firm_name", "firm"}:
            parsed["firm_name"] = cleaned_value
        elif normalized_key in {"firm_nick_name", "firm_nickname", "firm_nick_name_", "nickname", "nick_name"}:
            parsed["firm_nick_name"] = cleaned_value
        elif normalized_key in {"gstin", "gst_no", "gst_number"}:
            parsed["gst_no"] = cleaned_value
        elif normalized_key in {"zone", "region_name"}:
            parsed["zone"] = cleaned_value
        elif normalized_key in {"region", "area"}:
            parsed["region"] = cleaned_value
        elif normalized_key in {"credit_limit", "credit", "limit"}:
            try:
                parsed["credit_limit"] = float(cleaned_value)
            except (TypeError, ValueError):
                parsed["credit_limit"] = cleaned_value
        elif normalized_key in {"address", "street", "address_line"}:
            parsed["address"] = cleaned_value
        elif normalized_key in {"phone", "phone_number", "mobile", "contact_no", "contact_number"}:
            parsed["phone_number"] = cleaned_value
        elif normalized_key in {"email", "email_address"}:
            parsed["email"] = cleaned_value
    return parsed


def parse_retailer_fields_from_text(text: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip().lower().replace(" ", "_")
        cleaned_value = value.strip()
        if not cleaned_value:
            continue
        if normalized_key in {"retailer_name", "name", "retailer"} or normalized_key.endswith("retailer"):
            parsed["name"] = cleaned_value
        elif normalized_key in {"distributor", "linked_distributor", "linked_distributor_name", "distributor_name"} or normalized_key.endswith("distributor"):
            parsed["distributor_reference"] = cleaned_value
        elif normalized_key in {"location", "city", "place"}:
            parsed["location"] = cleaned_value
        elif normalized_key in {"address", "street", "address_line"}:
            parsed["address"] = cleaned_value
        elif normalized_key in {"phone", "phone_number", "mobile", "contact_no", "contact_number"}:
            parsed["phone_number"] = cleaned_value
        elif normalized_key in {"email", "email_address"}:
            parsed["email"] = cleaned_value
        elif normalized_key in {"gstin", "gst_no", "gst_number"}:
            parsed["gst_no"] = cleaned_value
    if not parsed.get("name") and lines:
        first_line = lines[0]
        if ":" in first_line:
            _, first_value = first_line.split(":", 1)
            parsed["name"] = first_value.strip()
    return parsed


__all__ = [
    "_extract_pdf_text",
    "_parse_pdf_table_like_text",
    "compare_step1",
    "compare_step2",
    "compare_step3",
    "run_full_verification",
    "parse_distributor_fields_from_text",
    "parse_retailer_fields_from_text",
]

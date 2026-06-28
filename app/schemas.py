from typing import Any


def validate_schema_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Schema payload must be a dictionary")
    return payload


def validate_record_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Record payload must be a dictionary")
    return payload

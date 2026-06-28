from typing import Any


def build_analytics_payload(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {"status": "ok", "args": args, "kwargs": kwargs}

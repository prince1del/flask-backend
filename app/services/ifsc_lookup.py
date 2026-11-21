"""IFSC → bank, branch and branch address (owner 21-11: "ifsc code dalne se bank ka address auto pull kyu nahi
hota"). Uses the free public IFSC directory (no key; only the bank's branch code is sent — no customer data).
Results are cached in memory; a failed lookup still returns the bank code read from the IFSC itself."""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL = 7 * 24 * 3600
_URL = "https://ifsc.razorpay.com/{ifsc}"


def normalize_ifsc(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())[:11]


def is_valid_ifsc(ifsc: str) -> bool:
    return bool(IFSC_RE.match(ifsc or ""))


def _s(v: Any) -> str:
    return str(v).strip() if v not in (None, "", False) else ""


def _fetch(ifsc: str) -> dict[str, Any] | None:
    req = Request(_URL.format(ifsc=ifsc), headers={"User-Agent": "nexora-books/1.0", "Accept": "application/json"})
    try:
        with urlopen(req, timeout=6) as r:  # noqa: S310 — fixed https host
            return json.loads(r.read().decode("utf-8", "replace"))
    except HTTPError as e:
        if e.code == 404:
            return {}
        return None
    except (URLError, TimeoutError, ValueError, OSError):
        return None


def lookup_ifsc(ifsc: str) -> dict[str, Any]:
    ifsc = normalize_ifsc(ifsc)
    out: dict[str, Any] = {"ifsc": ifsc, "ok": False, "valid_format": is_valid_ifsc(ifsc), "bank_code": ifsc[:4]}
    if not out["valid_format"]:
        out["message"] = "IFSC should be 11 characters, like SBIN0001234."
        return out
    hit = _CACHE.get(ifsc)
    if hit and time.time() - hit[0] < _TTL:
        return dict(hit[1])
    d = _fetch(ifsc)
    if d is None:
        out["message"] = "Couldn't reach the IFSC directory — please type the bank details."
        return out
    if not d:
        out["message"] = "This IFSC was not found — please check it."
        _CACHE[ifsc] = (time.time(), dict(out))
        return out
    branch = _s(d.get("BRANCH"))
    address = _s(d.get("ADDRESS"))
    city = _s(d.get("CITY")) or _s(d.get("DISTRICT"))
    state = _s(d.get("STATE"))
    tail = ", ".join(x for x in (city, state) if x and x.lower() not in address.lower())
    full_addr = ", ".join(x for x in (address, tail) if x)
    out.update({
        "ok": True,
        "bank_name": _s(d.get("BANK")),
        "branch": branch,
        "address": full_addr,
        "city": city,
        "district": _s(d.get("DISTRICT")),
        "state": state,
        "micr": _s(d.get("MICR")),
        "branch_with_address": ", ".join(x for x in (branch, full_addr) if x),
        "message": "Bank details filled from IFSC",
    })
    _CACHE[ifsc] = (time.time(), dict(out))
    return out

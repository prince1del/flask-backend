"""GSTIN decode + taxpayer lookup for party autofill."""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GSTIN_RE = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
)

# First 2 digits of GSTIN → state
GSTIN_STATE_CODES = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
}

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


def normalize_gstin(value: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(value or "")).upper()


def is_valid_gstin(gstin: str) -> bool:
    return bool(GSTIN_RE.match(gstin))


def decode_gstin_local(gstin: str) -> dict[str, Any]:
    """Always-available fields from GSTIN structure (no network)."""
    g = normalize_gstin(gstin)
    out: dict[str, Any] = {
        "gstin": g,
        "valid_format": is_valid_gstin(g),
        "state": None,
        "state_code": None,
        "pan": None,
        "source": "gstin_structure",
    }
    if len(g) >= 2:
        code = g[:2]
        out["state_code"] = code
        out["state"] = GSTIN_STATE_CODES.get(code)
    if len(g) >= 12:
        out["pan"] = g[2:12]
    return out


def _format_address(addr: dict | None) -> str:
    if not isinstance(addr, dict):
        return ""
    parts = [
        addr.get("flno") or addr.get("floor"),
        addr.get("bno") or addr.get("building") or addr.get("buildingNumber"),
        addr.get("bnm") or addr.get("buildingName"),
        addr.get("st") or addr.get("street"),
        addr.get("loc") or addr.get("locality") or addr.get("location"),
        addr.get("dst") or addr.get("district"),
        addr.get("city"),
        addr.get("stcd") or addr.get("state"),
        addr.get("pncd") or addr.get("pincode") or addr.get("zip"),
    ]
    line = " ".join(str(p).strip() for p in parts if p and str(p).strip())
    if line and "India" not in line:
        line = f"{line} India"
    return line.strip()


def _gst_type_from_duty(taxpayer: str | None) -> str | None:
    if not taxpayer:
        return None
    t = str(taxpayer).lower()
    if "comp" in t:
        return "Registered Business - Composition"
    if "unreg" in t:
        return "Unregistered"
    return "Registered Business - Regular"


def _parse_taxpayer_blob(data: dict[str, Any], source: str) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    pradr = data.get("pradr") if isinstance(data.get("pradr"), dict) else {}
    addr = pradr.get("addr") if isinstance(pradr.get("addr"), dict) else data.get("address")
    if isinstance(addr, str):
        address_text = addr
        state = data.get("stcd") or data.get("state")
        city = data.get("city") or data.get("loc")
    elif isinstance(addr, dict):
        address_text = _format_address(addr)
        state = addr.get("stcd") or data.get("stcd") or data.get("state")
        city = (addr.get("loc") or addr.get("city") or addr.get("dst") or "").strip() or None
    else:
        address_text = ""
        state = data.get("stcd") or data.get("state")
        city = None
    trade = data.get("tradeNam") or data.get("trade_name") or data.get("tradeName")
    legal = data.get("lgnm") or data.get("legal_name") or data.get("legalName")
    if not (trade or legal or address_text):
        return None
    taxpayer = data.get("dty") or data.get("taxpayer_type") or data.get("taxpayerType")
    status = data.get("sts") or data.get("status")
    return {
        "company": (trade or legal or "").strip() or None,
        "billing_name": (legal or trade or "").strip() or None,
        "address": address_text or None,
        "shipping_address": address_text or None,
        "city": city,
        "state": state,
        "gst_type": _gst_type_from_duty(str(taxpayer) if taxpayer else None),
        "status": status,
        "source": source,
    }


def _http_json(url: str, headers: dict[str, str] | None = None) -> Any:
    hdrs = {**_BROWSER_HEADERS, **(headers or {})}
    req = Request(url, headers=hdrs)
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _lookup_cleartax(gstin: str) -> dict[str, Any] | None:
    """Best-effort free public compliance report (no API key)."""
    url = f"https://cleartax.in/f/compliance-report/{gstin}/"
    raw = _http_json(url, {"Referer": "https://cleartax.in/"})
    if not isinstance(raw, dict):
        return None
    data = raw.get("taxpayerInfo") if isinstance(raw.get("taxpayerInfo"), dict) else raw
    return _parse_taxpayer_blob(data if isinstance(data, dict) else {}, "cleartax")


def _lookup_gstincheck(gstin: str, api_key: str) -> dict[str, Any] | None:
    """Optional provider when GSTIN_LOOKUP_API_KEY is set."""
    url = f"https://sheet.gstincheck.co.in/check/{api_key}/{gstin}"
    raw = _http_json(url)
    if not isinstance(raw, dict):
        return None
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    return _parse_taxpayer_blob(data if isinstance(data, dict) else {}, "gstincheck")


def _merge_online(result: dict[str, Any], online: dict[str, Any], local: dict[str, Any]) -> None:
    for k, v in online.items():
        if v not in (None, ""):
            result[k] = v
    if not result.get("state") and local.get("state"):
        result["state"] = local["state"]
    if not result.get("pan") and local.get("pan"):
        result["pan"] = local["pan"]
    result["message"] = "Details fetched — name, state & billing address filled."
    result["source"] = online.get("source") or result.get("source")


def lookup_gstin(gstin: str) -> dict[str, Any]:
    """
    Local GSTIN decode (state/PAN) + online taxpayer name/address.
    Tries free ClearTax report first; optional GSTIN_LOOKUP_API_KEY (gstincheck) as fallback.
    """
    local = decode_gstin_local(gstin)
    if not local["valid_format"]:
        return {**local, "ok": False, "message": "Invalid GSTIN format"}

    result: dict[str, Any] = {
        **local,
        "ok": True,
        "company": None,
        "billing_name": None,
        "address": None,
        "shipping_address": None,
        "city": None,
        "gst_type": "Registered Business - Regular",
        "message": "State/PAN filled from GSTIN.",
    }

    errors: list[str] = []

    try:
        online = _lookup_cleartax(local["gstin"])
        if online and (online.get("address") or online.get("company")):
            _merge_online(result, online, local)
            return result
        if online is None:
            errors.append("registry returned empty")
    except (HTTPError, URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
        errors.append(f"cleartax:{exc}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"cleartax:{exc}")

    api_key = (os.environ.get("GSTIN_LOOKUP_API_KEY") or os.environ.get("GSTINCHECK_API_KEY") or "").strip()
    if api_key:
        try:
            online = _lookup_gstincheck(local["gstin"], api_key)
            if online and (online.get("address") or online.get("company")):
                _merge_online(result, online, local)
                return result
        except (HTTPError, URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
            errors.append(f"gstincheck:{exc}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"gstincheck:{exc}")

    if errors:
        result["message"] = (
            "State/PAN filled from GSTIN. Full address lookup failed "
            f"({'; '.join(errors[:2])})."
        )
    else:
        result["message"] = "State/PAN filled from GSTIN. No address found for this GSTIN."
    return result

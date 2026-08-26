"""Best-effort WeTransfer link resolver for the Gmail CI/SO auto-import poller.

WeTransfer has no official public download API — this uses the same
request pattern their own web player uses internally (POST .../download
with the transfer id + security hash, which returns a direct_link to the
zipped/raw file). That's undocumented and can change without notice, so
every call here is expected to fail gracefully and just be skipped, never
to crash the poll.

Password-protected transfers are not supported (no way to know the
password automatically) — they're skipped with a clear error instead.
"""

from __future__ import annotations

import re
from typing import Any

LINK_RE = re.compile(
    r"https?://(?:www\.)?(?:we\.tl/[\w-]+|wetransfer\.com/downloads/[\w-]+(?:/[\w-]+){0,2})",
    re.IGNORECASE,
)
DOWNLOAD_URL_RE = re.compile(
    r"wetransfer\.com/downloads/([a-zA-Z0-9]+)/([a-zA-Z0-9]+)", re.IGNORECASE
)


def find_links(text: str) -> list[str]:
    """Unique WeTransfer / we.tl links found in email body text, in order."""
    if not text:
        return []
    seen: dict[str, None] = {}
    for match in LINK_RE.finditer(text):
        seen.setdefault(match.group(0), None)
    return list(seen.keys())


def _resolve_transfer_ids(link: str) -> tuple[str, str] | None:
    """Follow we.tl short links to the wetransfer.com/downloads/{id}/{hash} form."""
    import requests

    match = DOWNLOAD_URL_RE.search(link)
    if match:
        return match.group(1), match.group(2)

    resp = requests.get(link, allow_redirects=True, timeout=20)
    match = DOWNLOAD_URL_RE.search(resp.url)
    if match:
        return match.group(1), match.group(2)
    return None


def fetch_transfer_bytes(link: str) -> tuple[str, bytes]:
    """Download a WeTransfer link's content. Returns (filename, raw_bytes).

    Raises on any failure (expired link, password-protected, API shape
    changed, etc.) — callers must catch and skip, not propagate.
    """
    import requests

    ids = _resolve_transfer_ids(link)
    if not ids:
        raise ValueError(f"Could not resolve WeTransfer transfer id from: {link}")
    transfer_id, security_hash = ids

    api_url = f"https://wetransfer.com/api/v4/transfers/{transfer_id}/download"
    resp = requests.post(
        api_url,
        json={"security_hash": security_hash, "intent": "entire_transfer"},
        headers={
            "x-requested-with": "XMLHttpRequest",
            "content-type": "application/json",
        },
        timeout=30,
    )
    if resp.status_code == 401:
        raise ValueError("WeTransfer link is password-protected — cannot auto-download.")
    resp.raise_for_status()
    payload: dict[str, Any] = resp.json()
    direct_link = payload.get("direct_link") or payload.get("download_url")
    if not direct_link:
        raise RuntimeError(f"WeTransfer response missing a direct link: {payload}")

    file_resp = requests.get(direct_link, timeout=120)
    file_resp.raise_for_status()
    filename = f"wetransfer-{transfer_id}.zip"
    disposition = file_resp.headers.get("content-disposition") or ""
    fname_match = re.search(r'filename="?([^";]+)"?', disposition)
    if fname_match:
        filename = fname_match.group(1)
    return filename, file_resp.content

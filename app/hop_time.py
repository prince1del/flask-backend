"""India date for business entries: between 00:00 and 05:30 IST the server's UTC date is still yesterday."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def today_ist() -> date:
    return datetime.now(IST).date()


def today_iso() -> str:
    return today_ist().isoformat()

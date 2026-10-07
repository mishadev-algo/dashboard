"""Trading-day time zones used by account workers and dashboard views."""

from __future__ import annotations

from datetime import timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def day_zone(name: str) -> tzinfo:
    if not isinstance(name, str) or not name or len(name) > 100:
        raise ValueError("invalid day_timezone")
    # UTC needs no external time-zone database, including on Windows.
    if name == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"day_timezone {name!r} is unavailable; use an IANA name and install tzdata on Windows"
        ) from exc

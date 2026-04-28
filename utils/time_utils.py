"""
utils/time_utils.py
-------------------
Timezone-aware helpers for parsing the various time formats CREX uses.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from utils.logger import log


# CREX displays times in IST (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))


def parse_crex_datetime(raw: str) -> Optional[datetime]:
    """
    Try several common CREX date/time formats and return a UTC datetime.

    Handles:
    - "Apr 29, 2026, 02:30 PM IST"
    - "29 Apr 2026 14:30"
    - ISO 8601 strings from intercepted API responses
    - Epoch milliseconds (int or str)

    Returns None if parsing fails rather than raising.
    """
    if not raw:
        return None

    raw = raw.strip()

    # ── Epoch milliseconds ────────────────────────────────────────────────────
    if re.fullmatch(r"\d{10,13}", raw):
        ts = int(raw)
        if ts > 1e12:          # milliseconds
            ts /= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    # ── ISO 8601 ──────────────────────────────────────────────────────────────
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

    # ── "Apr 29, 2026, 02:30 PM IST" ─────────────────────────────────────────
    m = re.match(
        r"(\w{3})\s+(\d{1,2}),?\s+(\d{4}),?\s+(\d{1,2}):(\d{2})\s*(AM|PM)?(?:\s*IST)?",
        raw, re.IGNORECASE,
    )
    if m:
        month_str, day, year, hour, minute, ampm = m.groups()
        try:
            hour_int = int(hour)
            if ampm:
                if ampm.upper() == "PM" and hour_int != 12:
                    hour_int += 12
                elif ampm.upper() == "AM" and hour_int == 12:
                    hour_int = 0
            months = {
                "Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
                "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12,
            }
            dt = datetime(
                int(year), months[month_str[:3].capitalize()],
                int(day), hour_int, int(minute),
                tzinfo=IST,
            )
            return dt.astimezone(timezone.utc)
        except (KeyError, ValueError) as exc:
            log.debug("Date parse fallback failed for '{}': {}", raw, exc)

    log.warning("Could not parse date string: '{}'", raw)
    return None


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def to_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

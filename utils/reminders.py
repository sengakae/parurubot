"""Reminder time parsing and formatting, shared by the RemindMe cog and AI tools."""

import calendar
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_COUNTDOWN_RE = re.compile(
    r"^(?:(?P<days>\d+)d)?(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)?$",
    re.IGNORECASE,
)
_ISO_TZ_ABBREV_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)([A-Za-z/_+-]+)$"
)

_TZ_ABBREVS = {
    "UTC": "UTC",
    "GMT": "UTC",
    "PDT": "America/Los_Angeles",
    "PST": "America/Los_Angeles",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "CDT": "America/Chicago",
    "CST": "America/Chicago",
    "EDT": "America/New_York",
    "EST": "America/New_York",
    "JST": "Asia/Tokyo",
    "KST": "Asia/Seoul",
    "HKT": "Asia/Hong_Kong",
    "BST": "Europe/London",
    "CET": "Europe/Paris",
    "CEST": "Europe/Paris",
}

_FIXED_OFFSET_HOURS = {
    "UTC": 0,
    "GMT": 0,
    "PDT": -7,
    "PST": -8,
    "MDT": -6,
    "MST": -7,
    "CDT": -5,
    "CST": -6,
    "EDT": -4,
    "EST": -5,
    "BST": 1,
    "CET": 1,
    "CEST": 2,
    "JST": 9,
    "KST": 9,
    "HKT": 8,
}


def _to_utc_from_local_string(dt_str: str, tz_token: str) -> datetime | None:
    tz_key = tz_token.upper()
    tz_name = _TZ_ABBREVS.get(tz_key, tz_token)
    if len(dt_str) == 16:
        dt_str = f"{dt_str}:00"
    naive = datetime.fromisoformat(dt_str)

    try:
        local = naive.replace(tzinfo=ZoneInfo(tz_name))
        return local.astimezone(timezone.utc)
    except ZoneInfoNotFoundError:
        pass

    offset_hours = _FIXED_OFFSET_HOURS.get(tz_key)
    if offset_hours is not None:
        local = naive.replace(tzinfo=timezone(timedelta(hours=offset_hours)))
        return local.astimezone(timezone.utc)

    return None


def parse_countdown(value: str) -> timedelta | None:
    match = _COUNTDOWN_RE.fullmatch(value.strip())
    if not match:
        return None
    parts = {k: int(v) for k, v in match.groupdict().items() if v is not None}
    if not parts:
        return None
    return timedelta(
        days=parts.get("days", 0),
        hours=parts.get("hours", 0),
        minutes=parts.get("minutes", 0),
        seconds=parts.get("seconds", 0),
    )


def parse_remind_time(value: str) -> datetime | None:
    """Parse countdown (e.g. 2h15m) or datetime with timezone. Returns UTC-aware datetime."""
    value = value.strip()
    if not value:
        return None

    countdown = parse_countdown(value)
    if countdown is not None:
        return datetime.now(timezone.utc) + countdown

    if value.endswith("Z"):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
        except ValueError:
            pass

    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc)
    except ValueError:
        pass

    abbrev_match = _ISO_TZ_ABBREV_RE.match(value)
    if abbrev_match:
        dt_str, tz_token = abbrev_match.groups()
        try:
            return _to_utc_from_local_string(dt_str, tz_token)
        except ValueError:
            return None

    return None


_RECUR_RE = re.compile(r"^(?P<n>\d+)(?P<unit>[dwmy])$", re.IGNORECASE)
_RECUR_UNIT_NAMES = {"d": "day", "w": "week", "m": "month", "y": "year"}


def parse_recurrence(value: str) -> str | None:
    """Parse a recurrence like 2w (every 2 weeks). Returns normalized form or None."""
    match = _RECUR_RE.fullmatch(value.strip())
    if not match or int(match["n"]) <= 0:
        return None
    return f"{int(match['n'])}{match['unit'].lower()}"


def describe_recurrence(every: str) -> str:
    n, unit = int(every[:-1]), _RECUR_UNIT_NAMES[every[-1]]
    return f"every {unit}" if n == 1 else f"every {n} {unit}s"


def _add_months(dt: datetime, months: int) -> datetime:
    total = dt.month - 1 + months
    year, month = dt.year + total // 12, total % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def next_occurrence(anchor: datetime, every: str, after: datetime) -> datetime:
    """First occurrence of anchor + k*interval (k >= 1) strictly after `after`.

    Computed from the anchor each time so month-end days don't drift (Jan 31 -> Feb 28 -> Mar 31).
    """
    n, unit = int(every[:-1]), every[-1]
    k = 1
    while True:
        if unit == "d":
            candidate = anchor + timedelta(days=n * k)
        elif unit == "w":
            candidate = anchor + timedelta(weeks=n * k)
        elif unit == "m":
            candidate = _add_months(anchor, n * k)
        else:
            candidate = _add_months(anchor, 12 * n * k)
        if candidate > after:
            return candidate
        k += 1


def discord_timestamp(dt: datetime, style: str = "R") -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return f"<t:{int(dt.timestamp())}:{style}>"


def normalize_reminder_code(code: str) -> str:
    return code.strip("` ").lower()

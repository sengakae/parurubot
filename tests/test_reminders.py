from datetime import datetime, timedelta, timezone

import pytest

from utils.reminders import (describe_recurrence, next_occurrence, parse_countdown, reminder_zone,
                             parse_recurrence, parse_remind_time)

UTC = timezone.utc


def test_countdown():
    assert parse_countdown("2h30m") == timedelta(hours=2, minutes=30)
    assert parse_countdown("1d") == timedelta(days=1)
    assert parse_countdown("") is None and parse_countdown("soon") is None


def test_countdown_is_relative_to_now():
    before = datetime.now(UTC)
    when = parse_remind_time("10m")
    assert before + timedelta(minutes=10) <= when <= datetime.now(UTC) + timedelta(minutes=10)


@pytest.mark.parametrize("value, expected", [
    ("2026-12-01T09:00:00-08:00", datetime(2026, 12, 1, 17, 0, tzinfo=UTC)),  # numeric offset
    ("2026-12-01T17:00:00Z", datetime(2026, 12, 1, 17, 0, tzinfo=UTC)),
    ("2026-12-01T09:00PST", datetime(2026, 12, 1, 17, 0, tzinfo=UTC)),  # winter: UTC-8
    # "PST" means Pacific wall-clock time, so in summer it is daylight time (UTC-7)
    ("2026-07-01T09:00PST", datetime(2026, 7, 1, 16, 0, tzinfo=UTC)),
    ("2026-12-01T09:00JST", datetime(2026, 12, 1, 0, 0, tzinfo=UTC)),
])
def test_clock_times_convert_to_utc(value, expected):
    assert parse_remind_time(value) == expected


@pytest.mark.parametrize("value", [
    "2026-12-01T09:00",  # no time zone: the tool asks the user instead of guessing
    "2026-12-01T09:00XYZ",
    "next tuesday",
    "",
])
def test_unparseable_times(value):
    assert parse_remind_time(value) is None


def test_recurrence_parsing():
    assert parse_recurrence("2W") == "2w"
    assert parse_recurrence("1d") == "1d"
    assert parse_recurrence("0d") is None and parse_recurrence("every day") is None
    assert describe_recurrence("1d") == "every day"
    assert describe_recurrence("2w") == "every 2 weeks"


def test_monthly_recurrence_keeps_month_end():
    # Jan 31 -> Feb 28 -> Mar 31: computed from the anchor, so the day doesn't drift to 28
    anchor = datetime(2026, 1, 31, 9, 0, tzinfo=UTC)
    feb = next_occurrence(anchor, "1m", anchor)
    assert feb == datetime(2026, 2, 28, 9, 0, tzinfo=UTC)
    assert next_occurrence(anchor, "1m", feb) == datetime(2026, 3, 31, 9, 0, tzinfo=UTC)


def test_yearly_recurrence_from_leap_day():
    anchor = datetime(2024, 2, 29, 9, 0, tzinfo=UTC)
    assert next_occurrence(anchor, "1y", anchor) == datetime(2025, 2, 28, 9, 0, tzinfo=UTC)


def test_next_occurrence_skips_missed_ones():
    # the bot was down for 3 days: the next daily reminder is tomorrow's, not a backlog
    anchor = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    after = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    assert next_occurrence(anchor, "1d", after) == datetime(2026, 11, 5, 9, 0, tzinfo=UTC)


def test_daily_reminder_keeps_local_time_across_daylight_saving():
    # 9am PST on Mar 7 -> 9am PDT on Mar 9, not 10am: the clocks moved on Mar 8
    anchor = datetime(2026, 3, 7, 17, 0, tzinfo=UTC)  # 09:00 PST
    after = datetime(2026, 3, 8, 18, 0, tzinfo=UTC)
    nxt = next_occurrence(anchor, "1d", after, tz="America/Los_Angeles")
    assert nxt == datetime(2026, 3, 9, 16, 0, tzinfo=UTC)  # 09:00 PDT
    # without a zone it stays in UTC, as before
    assert next_occurrence(anchor, "1d", after) == datetime(2026, 3, 9, 17, 0, tzinfo=UTC)


@pytest.mark.parametrize("value, zone", [
    ("2026-12-01T09:00PST", "America/Los_Angeles"),
    ("2026-12-01T09:00America/Vancouver", "America/Vancouver"),
    ("2026-12-01T09:00-08:00", None),  # a bare offset can't tell when daylight saving starts
    ("2h", None),
])
def test_reminder_zone(value, zone):
    assert reminder_zone(value) == zone

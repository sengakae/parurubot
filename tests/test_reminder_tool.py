import asyncio
from types import SimpleNamespace as NS

import pytest

import utils.tools as tools


def run_set(monkeypatch, **args):
    created = []

    async def create_reminder(user_id, channel_id, message, remind_at, recur_every, recur_tz=None):
        created.append(recur_tz)
        return "abc123"

    monkeypatch.setattr(tools, "_remind_cog", lambda ctx: NS(create_reminder=create_reminder))
    ctx = NS(message=NS(author=NS(id=1), channel=NS(id=2)), actions=[])
    return asyncio.run(tools.set_reminder(ctx, "stretch", **args)), created


@pytest.mark.parametrize("args, zone", [
    ({"time": "2027-01-04T09:00-07:00", "every": "1d", "time_zone": "America/Vancouver"}, "America/Vancouver"),
    ({"time": "2027-01-04T09:00PST", "every": "1d"}, "America/Los_Angeles"),  # zone from the time
    ({"time": "2027-01-04T09:00-07:00", "every": "1d"}, None),  # bare offset: no zone known
])
def test_repeating_reminder_keeps_its_zone(monkeypatch, args, zone):
    result, created = run_set(monkeypatch, **args)
    assert result["id"] == "abc123" and created == [zone]


def test_unknown_zone_is_an_error(monkeypatch):
    result, created = run_set(monkeypatch, time="2027-01-04T09:00-07:00", every="1d", time_zone="Mars/Olympus")
    assert "Unknown time zone" in result["error"] and created == []

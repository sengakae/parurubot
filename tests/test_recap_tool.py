import asyncio
from types import SimpleNamespace as NS

import utils.tools as tools


def run_recap(monkeypatch, **args):
    fetches, summarized = [], []

    async def fake_fetch(channel, bot_user, limit, after=None):
        fetches.append((limit, after))
        return [{"author": "ben", "content": "hi", "timestamp": "2026-10-31T04:00:00"}]

    def fake_summarize(messages):
        summarized.append(messages)
        return "- ben said hi"

    monkeypatch.setattr(tools, "fetch_channel_messages", fake_fetch)
    monkeypatch.setattr(tools, "summarize_channel", fake_summarize)
    perms = NS(read_message_history=True)
    channel = NS(permissions_for=lambda _member: perms)
    ctx = NS(message=NS(channel=channel, author=NS()), bot=NS(user=NS()))
    result = asyncio.run(tools.read_recent_messages(ctx, **args))
    return result, fetches, summarized


def test_recap_returns_summary_command_output(monkeypatch):
    result, fetches, summarized = run_recap(monkeypatch)
    assert result == {"summary": "- ben said hi", "messages_analyzed": 1}
    assert fetches == [(100, None)]  # same default as !summary
    assert len(summarized) == 1


def test_recap_hours_reads_up_to_summary_limit(monkeypatch):
    _, fetches, _ = run_recap(monkeypatch, hours=24)
    limit, after = fetches[0]
    assert limit == 500 and after is not None


def test_recap_count_capped_like_summary(monkeypatch):
    _, fetches, _ = run_recap(monkeypatch, count=5000)
    assert fetches[0][0] == 500


def test_recap_no_messages(monkeypatch):
    async def empty(*a, **k):
        return []

    monkeypatch.setattr(tools, "fetch_channel_messages", empty)
    perms = NS(read_message_history=True)
    ctx = NS(message=NS(channel=NS(permissions_for=lambda _m: perms), author=NS()), bot=NS(user=NS()))
    assert asyncio.run(tools.read_recent_messages(ctx)) == {"error": "No messages found to summarize."}

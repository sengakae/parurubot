import asyncio
from types import SimpleNamespace as NS

import cogs.general as general

MESSAGES = [{"author": "ben", "content": "hi", "timestamp": "2026-10-31T04:00:00"}]


def test_long_summary_is_split_across_messages(monkeypatch):
    # a summary right at 2000 characters: with the header it no longer fits one message
    lines = [f"- topic {i} was discussed at some length" for i in range(50)]
    summary = "\n".join(lines)[:2000]
    sent = []

    async def fake_fetch(channel, bot_user, limit, after=None):
        return MESSAGES * 300

    async def send(text):
        sent.append(text)

    monkeypatch.setattr(general, "fetch_channel_messages", fake_fetch)
    monkeypatch.setattr(general, "summarize_channel", lambda messages: summary)
    perms = NS(read_message_history=True)
    ctx = NS(channel=NS(permissions_for=lambda _a: perms), author=NS(), send=send)
    asyncio.run(general.GeneralCog.summary.callback(general.GeneralCog(NS(user=NS())), ctx, "24h"))

    _analyzing, *parts = sent
    assert len(parts) == 2 and all(len(p) <= 2000 for p in parts)
    assert parts[0].startswith("**Channel Summary (300 messages analyzed):**")
    assert all(line.strip() in "\n".join(parts) for line in summary.splitlines())

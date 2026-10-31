import asyncio
from types import SimpleNamespace as NS

import cogs.general as general
import utils.ai as ai

MESSAGES = [{"author": "ben", "content": "hi", "timestamp": "2026-10-31T04:00:00"}]


def test_summarize_channel_respects_char_limit(monkeypatch):
    prompts = []

    def fake_generate(model, contents, config):
        prompts.append(contents)
        return "x" * 5000

    monkeypatch.setattr(ai, "generate_content_with_retry", fake_generate)
    text = ai.summarize_channel(MESSAGES, char_limit=1900)
    assert len(text) == 1900 and text.endswith("...")
    assert "under 1900 characters" in prompts[0]  # Gemini aims for the same limit


def test_summary_command_fits_discord_limit(monkeypatch):
    # summary right at CHAR_LIMIT: the header used to push the message past 2000
    sent = []

    async def fake_fetch(channel, bot_user, limit, after=None):
        return MESSAGES * 300

    def fake_summarize(messages, char_limit=ai.CHAR_LIMIT):
        return "x" * char_limit

    async def send(text):
        sent.append(text)

    monkeypatch.setattr(general, "fetch_channel_messages", fake_fetch)
    monkeypatch.setattr(general, "summarize_channel", fake_summarize)
    perms = NS(read_message_history=True)
    ctx = NS(channel=NS(permissions_for=lambda _a: perms), author=NS(), send=send)
    asyncio.run(general.GeneralCog.summary.callback(general.GeneralCog(NS(user=NS())), ctx, "24h"))
    assert sent[-1].startswith("**Channel Summary (300 messages analyzed):**")
    assert len(sent[-1]) <= 2000

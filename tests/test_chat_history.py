import asyncio
import contextlib
from types import SimpleNamespace as NS

import history
import main

AUTHOR = NS(id=1, display_name="ben")


def channel(sent):
    async def send(text):
        sent.append(text)

    @contextlib.asynccontextmanager
    async def typing():
        yield

    return NS(id=42, send=send, typing=typing)


def paruru(text, chan):
    return NS(content=f"paruru, {text}", attachments=[], reference=None, author=AUTHOR, channel=chan)


def test_follow_up_sees_the_earlier_question(monkeypatch):
    # "we cant assume all rolls go into speed" was answered without the original question
    history.reset_history()
    contexts = []

    async def fake_chat(prompt, tool_ctx, history_context, *rest):
        contexts.append(history_context)
        return f"answer {len(contexts)}"

    async def no_images(*_a):
        return []

    monkeypatch.setattr(main, "chat_with_ai", fake_chat)
    monkeypatch.setattr(main, "search_personal_notes", lambda *a, **k: None)
    monkeypatch.setattr(main, "collect_images_from_message", no_images)
    sent = []
    chan = channel(sent)

    asyncio.run(main.handle_ai_chat(paruru("chance of 22+ speed from 4 at +15?", chan)))
    asyncio.run(main.handle_ai_chat(paruru("we cant assume all rolls go into speed", chan)))

    lines = [p["text"] for p in contexts[1]["parts"]]
    assert lines[1:] == [
        "ben: paruru, chance of 22+ speed from 4 at +15?",
        "Bot: answer 1",
    ]
    # the current message is the prompt, not part of its own history
    assert not any("all rolls" in line for line in lines)
    assert sent == ["answer 1", "answer 2"]

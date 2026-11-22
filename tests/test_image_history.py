import asyncio
import contextlib
from types import SimpleNamespace as NS

from PIL import Image

import history
import main
import utils.links as links

AUTHOR = NS(id=1, display_name="ben")
PNG = "https://cdn.discordapp.com/attachments/1/2/cat.png"


def channel():
    async def send(text):
        pass

    @contextlib.asynccontextmanager
    async def typing():
        yield

    return NS(id=42, send=send, typing=typing)


def msg(content, chan, attachments=()):
    return NS(content=content, attachments=list(attachments), reference=None, author=AUTHOR, channel=chan)


def image(url):
    return NS(url=url, content_type="image/png")


def fake_downloads(monkeypatch, images_for):
    fetched = []

    async def download(url):
        fetched.append(url)
        return images_for.get(url)

    monkeypatch.setattr(links, "download_image_from_url", download)
    return fetched


def test_posted_images_are_not_downloaded(monkeypatch):
    # every screenshot in chat used to be downloaded and kept in memory
    history.reset_history()
    fetched = fake_downloads(monkeypatch, {})
    chan = channel()
    asyncio.run(main.on_message(msg("look at this", chan, [image(PNG)])))
    [entry] = history.get_channel_history(42, include_media=True)
    assert fetched == [] and entry["images"] == [PNG]


def run_chat(monkeypatch, images_for):
    contexts = []

    async def fake_chat(prompt, tool_ctx, history_context, *rest):
        contexts.append(history_context)
        return "ok"

    monkeypatch.setattr(main, "chat_with_ai", fake_chat)
    monkeypatch.setattr(main, "search_personal_notes", lambda *a, **k: None)
    fetched = fake_downloads(monkeypatch, images_for)
    asyncio.run(main.handle_ai_chat(msg("paruru, what's in the picture", channel())))
    image_parts = [p for p in contexts[0]["parts"] if "text" not in p]
    return fetched, image_parts


def test_only_the_newest_image_message_is_downloaded(monkeypatch):
    history.reset_history()
    history.add_message_to_history(42, "ben", "old pic", images=["https://x/old.png"])
    history.add_message_to_history(42, "ben", "new pic", images=["https://x/new.png"])
    fetched, parts = run_chat(monkeypatch, {"https://x/new.png": Image.new("RGB", (2, 2))})
    assert fetched == ["https://x/new.png"] and len(parts) == 1


def test_falls_back_when_the_newest_link_is_not_an_image(monkeypatch):
    # an article link or an expired Discord attachment downloads as nothing
    history.reset_history()
    history.add_message_to_history(42, "ben", "pic", images=["https://x/pic.png"])
    history.add_message_to_history(42, "ben", "read this", images=["https://news/article"])
    fetched, parts = run_chat(monkeypatch, {"https://x/pic.png": Image.new("RGB", (2, 2))})
    assert fetched == ["https://news/article", "https://x/pic.png"] and len(parts) == 1

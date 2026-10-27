import asyncio
from types import SimpleNamespace as NS

import utils.tools as tools
from tests.fake_store import FakeStore

ADMIN, ANTO, BEN = 1, 100, 200


def ctx(author_id, mentions=(), guild_id=5):
    message = NS(
        author=NS(id=author_id),
        guild=NS(id=guild_id),
        mentions=[NS(id=m, bot=False) for m in mentions],
        channel=NS(),
    )
    return tools.ToolContext(bot=NS(), message=message)


def test_link_name_non_admin_rejected(monkeypatch):
    monkeypatch.setenv("OWNER_ID", str(ADMIN))
    monkeypatch.setattr(tools, "memory_store", FakeStore())
    result = asyncio.run(tools.link_name(ctx(ANTO, mentions=[BEN]), name="ben"))
    assert result == {"error": "only the admin can link names"}


def test_link_name_requires_mention(monkeypatch):
    monkeypatch.setenv("OWNER_ID", str(ADMIN))
    monkeypatch.setattr(tools, "memory_store", FakeStore())
    result = asyncio.run(tools.link_name(ctx(ADMIN), name="ben"))
    assert "error" in result


def test_link_name_admin_links(monkeypatch):
    monkeypatch.setenv("OWNER_ID", str(ADMIN))
    store = FakeStore()
    monkeypatch.setattr(tools, "memory_store", store)
    result = asyncio.run(tools.link_name(ctx(ADMIN, mentions=[BEN]), name="Ben"))
    assert result == {"result": "linked"}
    assert asyncio.run(store.get_links(5)) == {"ben": BEN}


def test_recall_person_me(monkeypatch):
    store = FakeStore()
    asyncio.run(store.add_memory(5, ANTO, None, "fact", "likes tea", False, ANTO, None))
    asyncio.run(store.add_memory(5, ANTO, None, "fact", "chairman of kpmg", True, BEN, None))
    monkeypatch.setattr(tools, "memory_store", store)
    result = asyncio.run(tools.recall_person(ctx(ANTO), name="me"))
    assert result == {"memories": [
        {"content": "likes tea", "joke": False},
        {"content": "chairman of kpmg", "joke": True},
    ]}


def test_recall_person_nothing(monkeypatch):
    monkeypatch.setattr(tools, "memory_store", FakeStore())
    result = asyncio.run(tools.recall_person(ctx(ANTO), name="jules"))
    assert result == {"result": "nothing remembered"}


def test_declarations_match_handlers():
    assert {d["name"] for d in tools.TOOL_DECLARATIONS} == set(tools.HANDLERS)

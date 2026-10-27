from cogs.memories import (PAGE_SIZE, build_aboutme_embed, format_memory_line,
                           page_slice, remove_options, select_label)


def fact(i, content=None, joke=False, user_id=200):
    return {"id": i, "guild_id": 1, "user_id": user_id, "name": None, "kind": "fact",
            "content": content or f"fact {i}", "joke": joke, "added_by": 300,
            "source_message_id": None}


def name_row(i, name, user_id=200):
    return {"id": i, "guild_id": 1, "user_id": user_id, "name": name, "kind": "name",
            "content": name, "joke": False, "added_by": 300, "source_message_id": None}


def test_page_slice():
    memories = [fact(i) for i in range(30)]
    items, pages = page_slice(memories, 0)
    assert len(items) == PAGE_SIZE == 25 and pages == 2
    items, pages = page_slice(memories, 1)
    assert len(items) == 5
    assert page_slice([], 0) == ([], 1)


def test_format_memory_line_name_and_joke():
    assert format_memory_line(1, name_row(1, "ben")) == "1. goes by ben"
    assert format_memory_line(2, fact(2, "global chairman of kpmg", joke=True)) == "2. global chairman of kpmg"


def test_select_label_truncates():
    label = select_label(1, fact(1, "x" * 150))
    assert len(label) == 100


def test_locked_name_not_removable():
    items = [name_row(1, "sengakae"), name_row(2, "seng"), fact(3)]
    options = remove_options(items, 0)
    assert [memory_id for memory_id, _ in options] == [2, 3]
    assert options[0][1] == "2. goes by seng"


def test_embed_numbering_and_footer():
    memories = [fact(i) for i in range(30)]
    embed = build_aboutme_embed("anto", memories, 1)
    assert embed.title == "what i know about anto"
    assert embed.description.startswith("26. fact 25")
    assert embed.footer.text == "30 memories · page 2/2"


def test_embed_empty():
    embed = build_aboutme_embed("anto", [], 0)
    assert embed.description == "i don't know anything about you yet"


def test_view_components_layout_b():
    import asyncio
    from types import SimpleNamespace as NS

    from cogs.memories import AboutMeView
    from tests.fake_store import FakeStore

    async def build(memories):
        view = AboutMeView(NS(id=200, display_name="anto"), 1, memories, FakeStore())
        return [type(c).__name__ for c in view.children]

    assert asyncio.run(build([fact(i) for i in range(30)])) == [
        "RemoveMemorySelect", "AddMemoryButton", "PageButton", "PageButton"
    ]
    assert asyncio.run(build([fact(1)])) == ["RemoveMemorySelect", "AddMemoryButton"]
    assert asyncio.run(build([])) == ["AddMemoryButton"]
    assert asyncio.run(build([name_row(1, "sengakae")])) == ["AddMemoryButton"]

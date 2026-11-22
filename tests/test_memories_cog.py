import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import discord

import cogs.memories as memories_mod
from cogs.memories import MemoriesCog, member_name_map, should_queue
from tests.fake_store import FakeStore
from utils.memory_queue import QueuedMessage
from utils.memory_rules import Op


def discord_msg(content="hello", guild=True, bot=False):
    return NS(
        content=content,
        guild=NS(id=1) if guild else None,
        author=NS(bot=bot),
    )


def test_should_queue():
    assert should_queue(discord_msg("milo is going to the moon")) is True
    assert should_queue(discord_msg("hi", guild=False)) is False
    assert should_queue(discord_msg("hi", bot=True)) is False
    assert should_queue(discord_msg("!weather tokyo")) is False
    assert should_queue(discord_msg("   ")) is False
    assert should_queue(discord_msg("paruru, hi")) is True


def test_member_name_map():
    guild = NS(members=[
        NS(id=200, display_name="Milton", nick="milo", name="milo_99", global_name=None, bot=False),
        NS(id=300, display_name="tess", nick=None, name="tess", global_name="Tess", bot=False),
        NS(id=999, display_name="SomeBot", nick=None, name="somebot", global_name=None, bot=True),
    ])
    names = member_name_map(guild)
    assert names == {"milton": 200, "milo": 200, "milo_99": 200, "tess": 300}


def test_member_name_map_shared_name_is_ambiguous():
    guild = NS(members=[
        NS(id=200, display_name="milo", nick=None, name="milo_a", global_name=None, bot=False),
        NS(id=300, display_name="Milo", nick=None, name="milo_b", global_name=None, bot=False),
    ])
    names = member_name_map(guild)
    assert names["milo"] is None and names["milo_a"] == 200 and names["milo_b"] == 300


class FakeMessageRef:
    def __init__(self, reactions):
        self.reactions = reactions

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)


def make_guild(reactions):
    channel = NS(id=10, get_partial_message=lambda _id: FakeMessageRef(reactions))
    return NS(id=1, members=[], get_channel=lambda _id: channel)


def queue_messages(cog, count):
    for i in range(1, count + 1):
        cog.queue.add(QueuedMessage(
            id=i, channel_id=10, guild_id=1, author_id=100, author_label="rory",
            content=f"message {i}", mention_ids=[], reply_to_author_id=None,
            created_at=datetime.now(timezone.utc),
        ))


def test_process_guild_single_flight():
    cog = MemoriesCog(bot=NS(), store=FakeStore())
    started = []

    async def fake_process(guild):
        started.append(guild.id)

    cog.process_guild = fake_process
    guild = make_guild([])

    async def scenario():
        cog._running.add(guild.id)
        await cog.on_paruru_called(NS(guild=guild))
        await asyncio.sleep(0)
        cog._running.clear()
        await cog.on_paruru_called(NS(guild=guild))
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert started == [1]


def test_paruru_called_ignores_dms():
    cog = MemoriesCog(bot=NS(), store=FakeStore())
    asyncio.run(cog.on_paruru_called(NS(guild=None)))
    assert cog._running == set()


def test_process_guild_saves_without_reacting(monkeypatch):
    store = FakeStore()
    cog = MemoriesCog(bot=NS(), store=store)
    queue_messages(cog, 6)

    async def fake_extract(prompt):
        return [Op("add", 2, subject="self", content="likes tea")]

    monkeypatch.setattr(memories_mod, "extract_ops", fake_extract)
    reactions = []
    asyncio.run(cog.process_guild(make_guild(reactions)))

    assert reactions == []
    assert len(store.facts()) == 1
    assert not cog.queue.is_pending(3)
    assert cog.queue.is_pending(4)


def test_process_guild_failure_keeps_pending(monkeypatch):
    cog = MemoriesCog(bot=NS(), store=FakeStore())
    queue_messages(cog, 6)

    async def failing_extract(prompt):
        raise RuntimeError("api down")

    monkeypatch.setattr(memories_mod, "extract_ops", failing_extract)
    asyncio.run(cog.process_guild(make_guild([])))
    assert cog.queue.is_pending(1)


def test_on_ready_waits_for_schema(monkeypatch):
    import db

    calls = []

    async def fake_ensure(guild_id, store):
        calls.append(guild_id)

    monkeypatch.setattr(memories_mod, "ensure_locked_links", fake_ensure)
    monkeypatch.setattr(db, "pool", object())
    monkeypatch.setattr(db, "schema_ready", asyncio.Event())
    cog = MemoriesCog(bot=NS(guilds=[NS(id=1)]), store=FakeStore())

    async def scenario():
        task = asyncio.create_task(cog.on_ready())
        await asyncio.sleep(0.05)
        assert calls == []
        db.schema_ready.set()
        await task

    asyncio.run(scenario())
    assert calls == [1]


def test_reply_to_bot_not_a_link_target():
    from cogs.memories import _reply_author_id

    reply_to_bot = NS(reference=NS(resolved=NS(author=NS(id=999, bot=True))))
    reply_to_user = NS(reference=NS(resolved=NS(author=NS(id=200, bot=False))))
    assert _reply_author_id(reply_to_bot) is None
    assert _reply_author_id(reply_to_user) == 200
    assert _reply_author_id(NS(reference=None)) is None


def test_mention_names():
    from cogs.memories import _mention_names

    message = NS(mentions=[
        NS(id=200, display_name="Milton", name="milo_99", bot=False),
        NS(id=999, display_name="paruru", name="paruru", bot=True),
    ])
    assert _mention_names(message) == {"milton": 200, "milo_99": 200}


def test_is_direct():
    from cogs.memories import is_direct

    assert is_direct("paruru, milo is the llama keeper") is True
    assert is_direct("  Paruru, hi") is True
    assert is_direct("paruru is cool") is False
    assert is_direct("milo is the llama keeper") is False


def test_direct_statement_processed_on_its_own_call(monkeypatch):
    calls = []

    async def spy_extract(prompt):
        calls.append(prompt)
        return []

    monkeypatch.setattr(memories_mod, "extract_ops", spy_extract)
    cog = MemoriesCog(bot=NS(), store=FakeStore())
    message = NS(
        id=1, content="paruru, milo is the keeper of the royal llamas",
        clean_content="paruru, milo is the keeper of the royal llamas",
        guild=NS(id=1), author=NS(id=100, bot=False, display_name="rory"),
        channel=NS(id=10), mentions=[], reference=None,
        created_at=datetime.now(timezone.utc),
    )
    guild = NS(id=1, members=[], get_channel=lambda _id: None)

    async def scenario():
        await cog.on_message(message)
        await cog.process_guild(guild)

    asyncio.run(scenario())
    assert len(calls) == 1
    assert not cog.queue.is_pending(1)


DAILY_BLUE = (
    "Daily Blue #11: Beat Blue\n"
    "🟩🟩⬛🟩🟩🟩  5/6 left · 12 turns\n"
    "Streak 1 · #DailyBlue\n"
    "daily-red.com/blue"
)


def test_daily_game_scores_not_queued():
    assert should_queue(discord_msg(DAILY_BLUE)) is False
    wordle = "Wordle 1,234 3/6\n\n⬜🟨⬜⬜⬜\n⬜🟩🟩⬜🟨\n🟩🟩🟩🟩🟩"
    assert should_queue(discord_msg(wordle)) is False
    assert should_queue(discord_msg("🟥🟧🟦")) is False


def test_few_block_emojis_still_queued():
    assert should_queue(discord_msg("green means go 🟩")) is True
    assert should_queue(discord_msg("🟩🟩 two is fine")) is True


def test_describe_op_names_what_each_op_does():
    describe = memories_mod.describe_op
    assert describe(Op(op="add", message_id=5, subject="self", content="likes marzipan")) == (
        "add self 'likes marzipan' (msg 5)"
    )
    assert describe(Op(op="add", message_id=5, subject="name", name="milo", content="is a wizard", joke=True)) == (
        "add name milo 'is a wizard' joke (msg 5)"
    )
    assert describe(Op(op="update", message_id=6, memory_id=12, content="lives in atlantis")) == (
        "update #12 'lives in atlantis' (msg 6)"
    )
    assert describe(Op(op="forget", message_id=7, memory_id=12)) == "forget #12 (msg 7)"
    assert describe(Op(op="link", message_id=8, name="milo", user_id=111)) == "link milo -> 111 (msg 8)"


def queued(content, author_id=100):
    return QueuedMessage(id=1, channel_id=10, guild_id=1, author_id=author_id, author_label="x",
                         content=content, mention_ids=[], reply_to_author_id=None,
                         created_at=datetime.now(timezone.utc))


def test_relevant_memories_include_a_named_members_account():
    store = FakeStore()
    on_account = asyncio.run(store.add_memory(1, 300, None, "fact", "plays checkers", False, 300, None))
    under_name = asyncio.run(store.add_memory(1, None, "fern", "fact", "bought a kazoo", False, 100, None))
    cog = MemoriesCog(bot=NS(), store=store)
    rows = asyncio.run(cog._relevant_memories(1, [queued("fern is knitting again")], {"fern": 300}, {}))
    assert {r["id"] for r in rows} == {on_account, under_name}


def test_relevant_memories_skip_ambiguous_member_names():
    store = FakeStore()
    asyncio.run(store.add_memory(1, 300, None, "fact", "plays checkers", False, 300, None))
    under_name = asyncio.run(store.add_memory(1, None, "milo", "fact", "likes tea", False, 100, None))
    cog = MemoriesCog(bot=NS(), store=store)
    rows = asyncio.run(cog._relevant_memories(1, [queued("milo said hi")], {"milo": None}, {}))
    assert {r["id"] for r in rows} == {under_name}

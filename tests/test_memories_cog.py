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
    assert should_queue(discord_msg("ben is going to japan")) is True
    assert should_queue(discord_msg("hi", guild=False)) is False
    assert should_queue(discord_msg("hi", bot=True)) is False
    assert should_queue(discord_msg("!weather tokyo")) is False
    assert should_queue(discord_msg("   ")) is False
    # thread messages have a guild like any channel message
    assert should_queue(discord_msg("paruru, hi")) is True


def test_member_name_map():
    guild = NS(members=[
        NS(id=200, display_name="Benjamin", nick="ben", name="benj_99", global_name=None, bot=False),
        NS(id=300, display_name="cheung", nick=None, name="cheung", global_name="Cheung", bot=False),
        NS(id=999, display_name="SomeBot", nick=None, name="somebot", global_name=None, bot=True),
    ])
    names = member_name_map(guild)
    assert names == {"benjamin": 200, "ben": 200, "benj_99": 200, "cheung": 300}


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
            id=i, channel_id=10, guild_id=1, author_id=100, author_label="anto",
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
    assert cog.queue.is_pending(4)  # held back


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
        assert calls == []  # pool exists but tables aren't created yet
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
        NS(id=200, display_name="Benjamin", name="benj_99", bot=False),
        NS(id=999, display_name="paruru", name="paruru", bot=True),
    ])
    assert _mention_names(message) == {"benjamin": 200, "benj_99": 200}


def test_is_direct():
    from cogs.memories import is_direct

    assert is_direct("paruru, ben is the chairman") is True
    assert is_direct("  Paruru, hi") is True
    assert is_direct("paruru is cool") is False
    assert is_direct("ben is the chairman") is False


def test_direct_statement_processed_on_its_own_call(monkeypatch):
    calls = []

    async def spy_extract(prompt):
        calls.append(prompt)
        return []

    monkeypatch.setattr(memories_mod, "extract_ops", spy_extract)
    cog = MemoriesCog(bot=NS(), store=FakeStore())
    message = NS(
        id=1, content="paruru, ben is the global chairman of cover corp",
        clean_content="paruru, ben is the global chairman of cover corp",
        guild=NS(id=1), author=NS(id=100, bot=False, display_name="anto"),
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

"""Paruru's memories: learns from channel messages when the bot is called."""

import asyncio
import logging
import re

import discord
from discord.ext import commands

import db
from utils.identity import speaker_label
from utils.memory_learner import build_learner_prompt, extract_ops
from utils.memory_queue import PendingQueue, QueuedMessage
from utils.memory_rules import (LOCKED_NAMES, ApplyContext, apply_ops,
                                ensure_locked_links, remove_for_owner)

logger = logging.getLogger(__name__)

MEMORY_REACTION = "📝"
PAGE_SIZE = 25
SELECT_LABEL_LIMIT = 100
EMBED_DESCRIPTION_LIMIT = 4096
MEMORY_INPUT_LIMIT = 200
_WORD_RE = re.compile(r"[\w'-]+")


def should_queue(message) -> bool:
    return (
        message.guild is not None
        and not message.author.bot
        and bool(message.content.strip())
        and not message.content.lstrip().startswith("!")
    )


def member_name_map(guild) -> dict[str, int]:
    names = {}
    for member in guild.members:
        if member.bot:
            continue
        for name in (member.display_name, member.nick, member.name, member.global_name):
            if name:
                names.setdefault(name.casefold(), member.id)
    return names


def _mention_names(message) -> dict[str, int]:
    names = {}
    for user in message.mentions:
        if user.bot:
            continue
        for name in (user.display_name, user.name):
            if name:
                names.setdefault(name.casefold(), user.id)
    return names


def _reply_author_id(message) -> int | None:
    reference = message.reference
    resolved = getattr(reference, "resolved", None) if reference else None
    author = getattr(resolved, "author", None)
    if author is None or author.bot:
        return None
    return author.id


def page_slice(memories: list[dict], page: int) -> tuple[list[dict], int]:
    pages = max(1, -(-len(memories) // PAGE_SIZE))
    start = page * PAGE_SIZE
    return memories[start : start + PAGE_SIZE], pages


def format_memory_line(index: int, memory: dict) -> str:
    text = f"goes by {memory['content']}" if memory["kind"] == "name" else memory["content"]
    return f"{index}. {text}"


def select_label(index: int, memory: dict) -> str:
    return format_memory_line(index, memory)[:SELECT_LABEL_LIMIT]


def remove_options(items: list[dict], start: int) -> list[tuple[int, str]]:
    """(memory_id, label) for the page's removable memories; locked names can't be removed."""
    return [
        (memory["id"], select_label(start + i + 1, memory))
        for i, memory in enumerate(items)
        if not (memory["kind"] == "name" and memory["content"] in LOCKED_NAMES)
    ]


def build_aboutme_embed(display_name: str, memories: list[dict], page: int) -> discord.Embed:
    embed = discord.Embed(title=f"what i know about {display_name}")
    if not memories:
        embed.description = "i don't know anything about you yet"
        return embed
    items, pages = page_slice(memories, page)
    start = page * PAGE_SIZE
    lines = [format_memory_line(start + i + 1, m) for i, m in enumerate(items)]
    embed.description = "\n".join(lines)[:EMBED_DESCRIPTION_LIMIT]
    embed.set_footer(text=f"{len(memories)} memories · page {page + 1}/{pages}")
    return embed


class RemoveMemorySelect(discord.ui.Select):
    def __init__(self, options: list[tuple[int, str]]):
        super().__init__(
            placeholder="Remove a memory...",
            options=[discord.SelectOption(label=label, value=str(memory_id)) for memory_id, label in options],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        view: "AboutMeView" = self.view
        await remove_for_owner(view.guild_id, int(self.values[0]), view.owner.id, view.store)
        await view.refresh(interaction)


class AddMemoryModal(discord.ui.Modal, title="Add a memory"):
    def __init__(self, view: "AboutMeView"):
        super().__init__()
        self.about_view = view
        self.memory = discord.ui.TextInput(
            label="Memory", max_length=MEMORY_INPUT_LIMIT, placeholder="plays wuthering waves"
        )
        self.add_item(self.memory)

    async def on_submit(self, interaction: discord.Interaction):
        text = self.memory.value.strip()
        if not text:
            await interaction.response.send_message("memory can't be empty", ephemeral=True)
            return
        view = self.about_view
        await view.store.add_memory(
            view.guild_id, view.owner.id, None, "fact", text, False, view.owner.id, None
        )
        await view.refresh(interaction)


class AddMemoryButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Add memory", style=discord.ButtonStyle.success, row=1)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddMemoryModal(self.view))


class PageButton(discord.ui.Button):
    def __init__(self, label: str, step: int, disabled: bool):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=1, disabled=disabled)
        self.step = step

    async def callback(self, interaction: discord.Interaction):
        self.view.page += self.step
        await self.view.refresh(interaction)


class AboutMeView(discord.ui.View):
    def __init__(self, owner, guild_id: int, memories: list[dict], store=db):
        super().__init__(timeout=600)
        self.owner = owner
        self.guild_id = guild_id
        self.memories = memories
        self.store = store
        self.page = 0
        self.message = None
        self._build()

    def embed(self) -> discord.Embed:
        return build_aboutme_embed(self.owner.display_name, self.memories, self.page)

    def _build(self):
        self.clear_items()
        items, pages = page_slice(self.memories, self.page)
        options = remove_options(items, self.page * PAGE_SIZE)
        if options:
            self.add_item(RemoveMemorySelect(options))
        self.add_item(AddMemoryButton())
        if pages > 1:
            self.add_item(PageButton("◀", -1, disabled=self.page == 0))
            self.add_item(PageButton("▶", 1, disabled=self.page >= pages - 1))

    async def refresh(self, interaction: discord.Interaction):
        self.memories = await self.store.get_user_memories(self.guild_id, self.owner.id)
        _, pages = page_slice(self.memories, 0)
        self.page = min(self.page, pages - 1)
        self._build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("this isn't yours", ephemeral=True)
            return False
        return True


async def send_aboutme(channel, member, store=db) -> None:
    memories = await store.get_user_memories(channel.guild.id, member.id)
    view = AboutMeView(member, channel.guild.id, memories, store)
    view.message = await channel.send(embed=view.embed(), view=view)


class MemoriesCog(commands.Cog):
    def __init__(self, bot, store=db):
        self.bot = bot
        self.store = store
        self.queue = PendingQueue()
        self._running: set[int] = set()
        self._tasks: set[asyncio.Task] = set()
        self._ready = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._ready:
            return
        try:
            await asyncio.wait_for(db.schema_ready.wait(), timeout=30)
        except asyncio.TimeoutError:
            logger.warning("Memories: database not ready; locked names not ensured")
            return
        self._ready = True
        for guild in self.bot.guilds:
            await self._ensure_locked(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        if self._ready:
            await self._ensure_locked(guild)

    async def _ensure_locked(self, guild):
        try:
            await ensure_locked_links(guild.id, self.store)
        except Exception:
            logger.exception("Failed to ensure locked names for guild %s", guild.id)

    @commands.Cog.listener()
    async def on_message(self, message):
        if not should_queue(message):
            return
        self.queue.add(
            QueuedMessage(
                id=message.id,
                channel_id=message.channel.id,
                guild_id=message.guild.id,
                author_id=message.author.id,
                author_label=speaker_label(message.author),
                content=message.clean_content,
                mention_ids=[u.id for u in message.mentions if not u.bot],
                reply_to_author_id=_reply_author_id(message),
                created_at=message.created_at,
                mention_names=_mention_names(message),
            )
        )

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        content = payload.data.get("content")
        if content is not None:
            self.queue.edit(payload.message_id, content)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        self.queue.delete(payload.message_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        for message_id in payload.message_ids:
            self.queue.delete(message_id)

    def _is_own_reaction(self, payload) -> bool:
        return self.bot.user is not None and payload.user_id == self.bot.user.id

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if not self._is_own_reaction(payload):
            self.queue.react(payload.message_id, str(payload.emoji), 1)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        if not self._is_own_reaction(payload):
            self.queue.react(payload.message_id, str(payload.emoji), -1)

    @commands.Cog.listener()
    async def on_paruru_called(self, message):
        guild = message.guild
        if guild is None or guild.id in self._running:
            return
        self._running.add(guild.id)
        task = asyncio.create_task(self._run(guild))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, guild):
        try:
            await self.process_guild(guild)
        except Exception:
            logger.exception("Memory processing failed for guild %s", guild.id)
        finally:
            self._running.discard(guild.id)

    @commands.command(name="aboutme", help="Show and edit what paruru remembers about you")
    async def aboutme(self, ctx):
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return
        await send_aboutme(ctx.channel, ctx.author, self.store)

    async def _relevant_memories(self, guild_id, batch, member_names, links) -> list[dict]:
        user_ids = set()
        for m in batch:
            user_ids.add(m.author_id)
            user_ids.update(m.mention_ids)
            if m.reply_to_author_id is not None:
                user_ids.add(m.reply_to_author_id)

        unlinked_names = set()
        for m in batch:
            for word in _WORD_RE.findall(m.content.casefold()):
                if word in links:
                    user_ids.add(links[word])
                elif word in member_names:
                    unlinked_names.add(word)

        memories = {}
        for user_id in user_ids:
            for row in await self.store.get_user_memories(guild_id, user_id):
                memories[row["id"]] = row
        for name in unlinked_names:
            for row in await self.store.get_name_memories(guild_id, name):
                memories[row["id"]] = row
        return list(memories.values())

    async def process_guild(self, guild) -> None:
        member_names = member_name_map(guild)
        for channel_id in self.queue.channels_with_pending(guild.id):
            context, batch = self.queue.take_batch(channel_id)
            if not batch:
                continue
            links = await self.store.get_links(guild.id)
            memories = await self._relevant_memories(guild.id, batch, member_names, links)
            prompt = build_learner_prompt(
                context, batch, list(member_names), links, memories
            )
            try:
                ops = await extract_ops(prompt)
            except Exception:
                logger.exception("Memory learner failed for channel %s", channel_id)
                continue

            apply_ctx = ApplyContext(
                guild_id=guild.id,
                messages={m.id: m for m in batch},
                member_names=member_names,
                is_pending=self.queue.is_pending,
            )
            react_ids = await apply_ops(ops, apply_ctx, self.store)
            self.queue.mark_processed(channel_id, batch[-1].id)
            logger.info(
                "Memories: channel %s, %d messages, %d ops proposed, %d applied",
                channel_id,
                len(batch),
                len(ops),
                len(react_ids),
            )

            get_channel = getattr(guild, "get_channel_or_thread", guild.get_channel)
            channel = get_channel(channel_id)
            if channel is None:
                continue
            for message_id in react_ids:
                try:
                    await channel.get_partial_message(message_id).add_reaction(
                        MEMORY_REACTION
                    )
                except discord.HTTPException:
                    pass


async def setup(bot):
    await bot.add_cog(MemoriesCog(bot))

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
from utils.memory_rules import ApplyContext, apply_ops, ensure_locked_links

logger = logging.getLogger(__name__)

MEMORY_REACTION = "📝"
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


def _reply_author_id(message) -> int | None:
    reference = message.reference
    resolved = getattr(reference, "resolved", None) if reference else None
    author = getattr(resolved, "author", None)
    return author.id if author else None


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
        for _ in range(20):
            if db.pool is not None:
                break
            await asyncio.sleep(0.25)
        else:
            logger.warning("Memories: database pool not ready; locked names not ensured")
            return
        self._ready = True
        for guild in self.bot.guilds:
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

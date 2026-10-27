import asyncio
import logging
from datetime import datetime, timezone

from discord.ext import commands

import db
from utils.reminders import (describe_recurrence, discord_timestamp, next_occurrence,
                             normalize_reminder_code, parse_recurrence, parse_remind_time)

logger = logging.getLogger(__name__)


class RemindMeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._tasks: dict[int, asyncio.Task] = {}
        self._restored = False

    def cog_unload(self):
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._restored:
            return
        for _ in range(20):
            if db.pool is not None:
                self._restored = True
                await self._restore_reminders()
                return
            await asyncio.sleep(0.25)
        logger.warning("RemindMe: database pool not ready; reminders not restored")

    async def _restore_reminders(self):
        rows = await db.get_all_reminders()
        for row in rows:
            if row["id"] in self._tasks:
                continue
            remind_at = row["remind_at"]
            if remind_at.tzinfo is None:
                remind_at = remind_at.replace(tzinfo=timezone.utc)
            else:
                remind_at = remind_at.astimezone(timezone.utc)
            self._schedule(
                row["id"],
                row["channel_id"],
                row["user_id"],
                row["message"],
                remind_at,
                row["recur_every"],
                row["recur_anchor"],
            )
        if rows:
            logger.info("Restored %d reminder(s) from database", len(rows))

    def _schedule(
        self,
        reminder_id,
        channel_id,
        user_id,
        message,
        remind_at: datetime,
        recur_every: str | None = None,
        recur_anchor: datetime | None = None,
    ):
        if reminder_id in self._tasks:
            return

        async def _fire():
            delay = (remind_at - datetime.now(timezone.utc)).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)

            try:
                await self._deliver(channel_id, user_id, message)
            except Exception:
                logger.exception("Failed to deliver reminder %s", reminder_id)

            self._tasks.pop(reminder_id, None)
            next_at = None
            try:
                if recur_every:
                    next_at = next_occurrence(
                        recur_anchor, recur_every, datetime.now(timezone.utc)
                    )
                    await db.update_reminder_time(reminder_id, next_at)
                else:
                    await db.delete_reminder(reminder_id)
            except Exception:
                logger.exception("Failed to update reminder %s in database", reminder_id)

            if next_at is not None:
                self._schedule(
                    reminder_id,
                    channel_id,
                    user_id,
                    message,
                    next_at,
                    recur_every,
                    recur_anchor,
                )

        self._tasks[reminder_id] = asyncio.create_task(_fire())

    async def _deliver(self, channel_id, user_id, message):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                logger.warning("Reminder channel %s not found", channel_id)
                return

        mention = f"<@{user_id}>"
        try:
            user = await self.bot.fetch_user(user_id)
            mention = user.mention
        except Exception:
            pass

        await channel.send(f"{mention} **Reminder:** {message}")

    async def create_reminder(
        self, user_id, channel_id, message, remind_at: datetime, recur_every=None
    ) -> str:
        """Store and schedule a reminder. Returns its public code."""
        recur_anchor = remind_at if recur_every else None
        reminder_id, code = await db.add_reminder(
            user_id, channel_id, message, remind_at, recur_every, recur_anchor
        )
        self._schedule(
            reminder_id,
            channel_id,
            user_id,
            message,
            remind_at,
            recur_every,
            recur_anchor,
        )
        return code

    async def cancel_reminder_for_user(self, user_id, code):
        """Cancel the user's reminder with this code. Returns the deleted row, or None."""
        row = await db.get_reminder_by_code(normalize_reminder_code(code))
        if row is None or row["user_id"] != user_id:
            return None

        task = self._tasks.pop(row["id"], None)
        if task is not None:
            task.cancel()
        await db.delete_reminder(row["id"])
        return row

    async def _user_label(self, user_id: int, guild) -> str:
        if guild:
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except Exception:
                    member = None
            if member:
                return member.display_name
        try:
            user = await self.bot.fetch_user(user_id)
            return user.global_name or user.name
        except Exception:
            return f"user {user_id}"

    @commands.command(
        name="remindme",
        help=(
            "Set a reminder: !remindme <message> <time> [every <n>d|w|m|y] "
            "(e.g. 2h15m or 2026-05-31T16:00PDT; add `every 2w` to repeat)"
        ),
    )
    async def remindme(self, ctx, *, args: str):
        usage = (
            "Usage: `!remindme <message> <time> [every <interval>]`\n"
            "Time can be a countdown (`2h15m`, `30m`, `1d2h`) or a datetime with timezone "
            "(`2026-05-31T16:00PDT`).\n"
            "Repeat with `every <n>d`, `<n>w`, `<n>m` (months), or `<n>y` — e.g. "
            "`!remindme pay rent 2026-11-01T09:00PST every 1m`. "
            "If the time is omitted, the first reminder is one interval from now."
        )

        recur_every = None
        tokens = args.rsplit(maxsplit=2)
        if len(tokens) == 3 and tokens[1].lower() == "every":
            recur_every = parse_recurrence(tokens[2])
            if recur_every is None:
                await ctx.send(
                    "Could not parse that interval. Use `every <n>d`, `<n>w`, `<n>m`, or `<n>y` "
                    "(e.g. `every 2w`)."
                )
                return
            args = tokens[0]

        now = datetime.now(timezone.utc)
        parts = args.rsplit(maxsplit=1)
        remind_at = parse_remind_time(parts[-1]) if len(parts) == 2 else None
        if remind_at is not None:
            message = parts[0].strip()
        elif recur_every is not None:
            message = args.strip()
            remind_at = next_occurrence(now, recur_every, now)
        elif len(parts) < 2:
            await ctx.send(usage)
            return
        else:
            await ctx.send(
                "Could not parse that time. Use a countdown like `2h15m` or a datetime like "
                "`2026-05-31T16:00PDT`."
            )
            return

        if not message:
            await ctx.send("Reminder message cannot be empty.")
            return

        if remind_at <= now:
            await ctx.send("That time is in the past. Pick a future time.")
            return

        code = await self.create_reminder(
            ctx.author.id, ctx.channel.id, message, remind_at, recur_every
        )

        when = discord_timestamp(remind_at)
        repeat = f" and then {describe_recurrence(recur_every)}" if recur_every else ""
        await ctx.send(
            f"Got it — I'll remind you {when}{repeat}: **{message}**\n"
            f"ID `{code}` — cancel with `!cancelreminder {code}`."
        )

    @commands.command(
        name="cancelreminder",
        help="Cancel one of your reminders: !cancelreminder <id> (see !myreminders)",
    )
    async def cancelreminder(self, ctx, code: str):
        row = await self.cancel_reminder_for_user(ctx.author.id, code)
        if row is None:
            await ctx.send(
                f"You have no reminder with ID `{normalize_reminder_code(code)}`. "
                "See `!myreminders`."
            )
            return

        await ctx.send(f"Cancelled reminder `{row['code']}`: **{row['message']}**")

    @commands.command(
        name="myreminders",
        help="List all reminders you've created, with their IDs",
    )
    async def myreminders(self, ctx):
        rows = await db.get_user_reminders(ctx.author.id)
        if not rows:
            await ctx.send("You have no reminders.")
            return

        lines = ["**Your reminders**"]
        length = len(lines[0])
        for i, row in enumerate(rows, start=1):
            when = discord_timestamp(row["remind_at"])
            repeat = (
                f", {describe_recurrence(row['recur_every'])}"
                if row["recur_every"]
                else ""
            )
            line = (
                f"{i}. {when} — **{row['message']}** "
                f"(<#{row['channel_id']}>{repeat}, ID `{row['code']}`)"
            )
            # Stay under Discord's 2000-character message limit.
            if length + len(line) + 1 > 1900:
                lines.append(f"…and {len(rows) - i + 1} more")
                break
            lines.append(line)
            length += len(line) + 1

        await ctx.send("\n".join(lines))

    @commands.command(
        name="timers",
        help="List the next 5 upcoming reminders",
    )
    async def timers(self, ctx):
        rows = await db.get_upcoming_reminders(limit=5)
        if not rows:
            await ctx.send("No upcoming reminders.")
            return

        lines = ["**Upcoming reminders**"]
        for i, row in enumerate(rows, start=1):
            remind_at = row["remind_at"]
            if remind_at.tzinfo is None:
                remind_at = remind_at.replace(tzinfo=timezone.utc)
            else:
                remind_at = remind_at.astimezone(timezone.utc)

            when = discord_timestamp(remind_at)
            user = await self._user_label(row["user_id"], ctx.guild)
            channel = f"<#{row['channel_id']}>"
            repeat = (
                f", {describe_recurrence(row['recur_every'])}"
                if row["recur_every"]
                else ""
            )
            lines.append(
                f"{i}. {when} — **{row['message']}** ({user}, {channel}{repeat}, ID `{row['code']}`)"
            )

        await ctx.send("\n".join(lines))


async def setup(bot):
    await bot.add_cog(RemindMeCog(bot))

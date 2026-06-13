import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from discord.ext import commands

import db

logger = logging.getLogger(__name__)

_COUNTDOWN_RE = re.compile(
    r"^(?:(?P<days>\d+)d)?(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)?$",
    re.IGNORECASE,
)
_ISO_TZ_ABBREV_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)([A-Za-z/_+-]+)$"
)

_TZ_ABBREVS = {
    "UTC": "UTC",
    "GMT": "UTC",
    "PDT": "America/Los_Angeles",
    "PST": "America/Los_Angeles",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "CDT": "America/Chicago",
    "CST": "America/Chicago",
    "EDT": "America/New_York",
    "EST": "America/New_York",
    "JST": "Asia/Tokyo",
    "KST": "Asia/Seoul",
    "HKT": "Asia/Hong_Kong",
    "BST": "Europe/London",
    "CET": "Europe/Paris",
    "CEST": "Europe/Paris",
}

_FIXED_OFFSET_HOURS = {
    "UTC": 0,
    "GMT": 0,
    "PDT": -7,
    "PST": -8,
    "MDT": -6,
    "MST": -7,
    "CDT": -5,
    "CST": -6,
    "EDT": -4,
    "EST": -5,
    "BST": 1,
    "CET": 1,
    "CEST": 2,
    "JST": 9,
    "KST": 9,
    "HKT": 8,
}


def _to_utc_from_local_string(dt_str: str, tz_token: str) -> datetime | None:
    tz_key = tz_token.upper()
    tz_name = _TZ_ABBREVS.get(tz_key, tz_token)
    if len(dt_str) == 16:
        dt_str = f"{dt_str}:00"
    naive = datetime.fromisoformat(dt_str)

    try:
        local = naive.replace(tzinfo=ZoneInfo(tz_name))
        return local.astimezone(timezone.utc)
    except ZoneInfoNotFoundError:
        pass

    offset_hours = _FIXED_OFFSET_HOURS.get(tz_key)
    if offset_hours is not None:
        local = naive.replace(tzinfo=timezone(timedelta(hours=offset_hours)))
        return local.astimezone(timezone.utc)

    return None


def parse_countdown(value: str) -> timedelta | None:
    match = _COUNTDOWN_RE.fullmatch(value.strip())
    if not match:
        return None
    parts = {k: int(v) for k, v in match.groupdict().items() if v is not None}
    if not parts:
        return None
    return timedelta(
        days=parts.get("days", 0),
        hours=parts.get("hours", 0),
        minutes=parts.get("minutes", 0),
        seconds=parts.get("seconds", 0),
    )


def parse_remind_time(value: str) -> datetime | None:
    """Parse countdown (e.g. 2h15m) or datetime with timezone. Returns UTC-aware datetime."""
    value = value.strip()
    if not value:
        return None

    countdown = parse_countdown(value)
    if countdown is not None:
        return datetime.now(timezone.utc) + countdown

    if value.endswith("Z"):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
        except ValueError:
            pass

    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc)
    except ValueError:
        pass

    abbrev_match = _ISO_TZ_ABBREV_RE.match(value)
    if abbrev_match:
        dt_str, tz_token = abbrev_match.groups()
        try:
            return _to_utc_from_local_string(dt_str, tz_token)
        except ValueError:
            return None

    return None


def discord_timestamp(dt: datetime, style: str = "R") -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return f"<t:{int(dt.timestamp())}:{style}>"


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
            )
        if rows:
            logger.info("Restored %d reminder(s) from database", len(rows))

    def _schedule(self, reminder_id, channel_id, user_id, message, remind_at: datetime):
        if reminder_id in self._tasks:
            return

        async def _fire():
            try:
                delay = (remind_at - datetime.now(timezone.utc)).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)
                await self._deliver(channel_id, user_id, message)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to deliver reminder %s", reminder_id)
            finally:
                try:
                    await db.delete_reminder(reminder_id)
                except Exception:
                    logger.exception(
                        "Failed to delete reminder %s from database", reminder_id
                    )
                self._tasks.pop(reminder_id, None)

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
        help="Set a reminder: !remindme <message> <time> (e.g. 2h15m or 2026-05-31T16:00PDT)",
    )
    async def remindme(self, ctx, *, args: str):
        parts = args.rsplit(maxsplit=1)
        if len(parts) < 2:
            await ctx.send(
                "Usage: `!remindme <message> <time>`\n"
                "Time can be a countdown (`2h15m`, `30m`, `1d2h`) or a datetime with timezone "
                "(`2026-05-31T16:00PDT`)."
            )
            return

        message, time_str = parts[0].strip(), parts[1].strip()
        if not message:
            await ctx.send("Reminder message cannot be empty.")
            return

        remind_at = parse_remind_time(time_str)
        if remind_at is None:
            await ctx.send(
                "Could not parse that time. Use a countdown like `2h15m` or a datetime like "
                "`2026-05-31T16:00PDT`."
            )
            return

        now = datetime.now(timezone.utc)
        if remind_at <= now:
            await ctx.send("That time is in the past. Pick a future time.")
            return

        reminder_id = await db.add_reminder(
            ctx.author.id,
            ctx.channel.id,
            message,
            remind_at,
        )
        self._schedule(
            reminder_id,
            ctx.channel.id,
            ctx.author.id,
            message,
            remind_at,
        )

        when = discord_timestamp(remind_at)
        await ctx.send(f"Got it — I'll remind you {when}: **{message}**")

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
            lines.append(
                f"{i}. {when} — **{row['message']}** ({user}, {channel})"
            )

        await ctx.send("\n".join(lines))


async def setup(bot):
    await bot.add_cog(RemindMeCog(bot))

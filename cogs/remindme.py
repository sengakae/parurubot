import asyncio
import calendar
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


_RECUR_RE = re.compile(r"^(?P<n>\d+)(?P<unit>[dwmy])$", re.IGNORECASE)
_RECUR_UNIT_NAMES = {"d": "day", "w": "week", "m": "month", "y": "year"}


def parse_recurrence(value: str) -> str | None:
    """Parse a recurrence like 2w (every 2 weeks). Returns normalized form or None."""
    match = _RECUR_RE.fullmatch(value.strip())
    if not match or int(match["n"]) <= 0:
        return None
    return f"{int(match['n'])}{match['unit'].lower()}"


def describe_recurrence(every: str) -> str:
    n, unit = int(every[:-1]), _RECUR_UNIT_NAMES[every[-1]]
    return f"every {unit}" if n == 1 else f"every {n} {unit}s"


def _add_months(dt: datetime, months: int) -> datetime:
    total = dt.month - 1 + months
    year, month = dt.year + total // 12, total % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def next_occurrence(anchor: datetime, every: str, after: datetime) -> datetime:
    """First occurrence of anchor + k*interval (k >= 1) strictly after `after`.

    Computed from the anchor each time so month-end days don't drift (Jan 31 -> Feb 28 -> Mar 31).
    """
    n, unit = int(every[:-1]), every[-1]
    k = 1
    while True:
        if unit == "d":
            candidate = anchor + timedelta(days=n * k)
        elif unit == "w":
            candidate = anchor + timedelta(weeks=n * k)
        elif unit == "m":
            candidate = _add_months(anchor, n * k)
        else:
            candidate = _add_months(anchor, 12 * n * k)
        if candidate > after:
            return candidate
        k += 1


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

        recur_anchor = remind_at if recur_every else None
        reminder_id, code = await db.add_reminder(
            ctx.author.id,
            ctx.channel.id,
            message,
            remind_at,
            recur_every,
            recur_anchor,
        )
        self._schedule(
            reminder_id,
            ctx.channel.id,
            ctx.author.id,
            message,
            remind_at,
            recur_every,
            recur_anchor,
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
        code = code.strip("`").lower()
        row = await db.get_reminder_by_code(code)
        if row is None or row["user_id"] != ctx.author.id:
            await ctx.send(f"You have no reminder with ID `{code}`. See `!myreminders`.")
            return

        task = self._tasks.pop(row["id"], None)
        if task is not None:
            task.cancel()
        await db.delete_reminder(row["id"])
        await ctx.send(f"Cancelled reminder `{code}`: **{row['message']}**")

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

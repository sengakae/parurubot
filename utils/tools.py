"""Functions the 'paruru,' chat model can call.

Caller identity (author, channel) always comes from ToolContext, never from model
arguments, so a user can only act on their own reminders and the current channel.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import db
from cogs.memories import send_aboutme
from utils.ai import (MODEL, _generate_with_error_retry, extract_response_text,
                      grounding_config)
from utils.memory_rules import admin_link, recall
from utils.messages import fetch_channel_messages
from utils.notes import search_personal_notes
from utils.reminders import (describe_recurrence, discord_timestamp, next_occurrence,
                             normalize_reminder_code, parse_recurrence, parse_remind_time)
from utils.weather import fetch_weather

logger = logging.getLogger(__name__)

MAX_RECAP_MESSAGES = 200
MAX_RECAP_CHARS = 300

TOOL_GUIDANCE = (
    "\n\nYou can call tools. Use search_web for current events or facts you aren't sure of, "
    "search_notes for anything that might be in your personal notes, get_weather for weather, "
    "the reminder tools when someone asks to be reminded or to see/cancel their reminders, and "
    "read_recent_messages when asked about what was said in this channel. "
    "For reminders at a clock time, convert to ISO 8601 with a numeric UTC offset; if the user "
    "didn't say their timezone, ask them instead of guessing. "
    "When you set or list reminders, tell the user the ID and show the 'when' value exactly as "
    "given (it is a Discord timestamp that renders in their local time). "
    "Use recall_person when someone asks about a person (or about themselves, with name \"me\"). "
    "Memories marked joke are running bits: bring them up playfully, never state them as fact. "
    "Use show_my_memories when someone asks to see their memories, and link_name when the "
    "admin tells you \"@user is <name>\". "
    "Don't call tools for casual chat."
)

# Store for the memory tools; tests swap in a fake.
memory_store = db

TOOL_DECLARATIONS = [
    {
        "name": "search_web",
        "description": "Search the web for current or factual information.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_notes",
        "description": "Search your personal notes for relevant information.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look up."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name, e.g. Tokyo."}
            },
            "required": ["city"],
        },
    },
    {
        "name": "set_reminder",
        "description": "Set a reminder for the user who is talking to you, in this channel.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "What to remind them about."},
                "time": {
                    "type": "string",
                    "description": (
                        "When to remind: a countdown like 2h15m, 30m, 1d2h, or an ISO 8601 "
                        "datetime with a numeric offset like 2026-11-01T09:00-07:00. "
                        "May be omitted only when 'every' is set (first reminder is one "
                        "interval from now)."
                    ),
                },
                "every": {
                    "type": "string",
                    "description": (
                        "Optional repeat interval: <n>d, <n>w, <n>m (months) or <n>y, "
                        "e.g. 1w for weekly."
                    ),
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "list_my_reminders",
        "description": "List the reminders of the user who is talking to you, with their IDs.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_reminder",
        "description": "Cancel one of the user's reminders by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The reminder ID, e.g. a1b2c3."}
            },
            "required": ["code"],
        },
    },
    {
        "name": "read_recent_messages",
        "description": (
            "Read recent messages in this channel. Give either hours (messages from the "
            "last N hours) or count (the last N messages)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "description": "Look back this many hours."},
                "count": {"type": "integer", "description": "Number of recent messages."},
            },
        },
    },
    {
        "name": "recall_person",
        "description": (
            "Look up what you remember about a person in this server. Use the name as "
            "the user wrote it, or \"me\" for the person talking to you."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "A name, or \"me\"."}
            },
            "required": ["name"],
        },
    },
    {
        "name": "show_my_memories",
        "description": "Post the memories embed for the person talking to you.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "link_name",
        "description": (
            "Admin only: link a name to the user @mentioned in the message, e.g. "
            "\"@xCooki3s is ben\"."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The name to link."}
            },
            "required": ["name"],
        },
    },
]


@dataclass
class ToolContext:
    bot: object
    message: object
    # Side effects already applied (e.g. "set reminder `ab12cd`"), so callers know
    # not to re-run the request and can report them if the reply never arrives.
    actions: list[str] = field(default_factory=list)


def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="minutes")


async def search_web(ctx, query):
    # Single grounded request (gemini-2.5 can't mix google_search with function
    # calling). No empty-response retries: the outer model can answer without it.
    response = await asyncio.to_thread(
        _generate_with_error_retry,
        model=MODEL,
        contents=query,
        config=grounding_config,
    )
    text = extract_response_text(response)
    if not text:
        return {"error": "The search returned nothing."}
    return {"result": text}


async def search_notes(ctx, query):
    notes = await asyncio.to_thread(search_personal_notes, query, 3)
    return {"result": notes or "No relevant notes found."}


async def get_weather(ctx, city):
    weather = await asyncio.to_thread(fetch_weather, city)
    if weather is None:
        return {"error": f"City not found: {city}"}
    return weather


def _remind_cog(ctx):
    cog = ctx.bot.get_cog("RemindMeCog")
    if cog is None:
        raise RuntimeError("Reminders are not available right now.")
    return cog


async def set_reminder(ctx, message, time=None, every=None):
    recur_every = None
    if every:
        recur_every = parse_recurrence(every)
        if recur_every is None:
            return {"error": f"Could not parse interval '{every}'. Use e.g. 1d, 2w, 1m, 1y."}

    now = datetime.now(timezone.utc)
    if time:
        remind_at = parse_remind_time(time)
        if remind_at is None:
            return {
                "error": f"Could not parse time '{time}'. Use a countdown like 2h15m or "
                "ISO 8601 with offset like 2026-11-01T09:00-07:00."
            }
    elif recur_every:
        remind_at = next_occurrence(now, recur_every, now)
    else:
        return {"error": "A time is required unless 'every' is set."}

    message = message.strip()
    if not message:
        return {"error": "Reminder message cannot be empty."}
    if remind_at <= now:
        return {"error": "That time is in the past."}

    code = await _remind_cog(ctx).create_reminder(
        ctx.message.author.id, ctx.message.channel.id, message, remind_at, recur_every
    )
    ctx.actions.append(f"set reminder `{code}` {discord_timestamp(remind_at)}: {message}")
    result = {
        "id": code,
        "message": message,
        "when": discord_timestamp(remind_at),
        "remind_at_utc": _iso_utc(remind_at),
    }
    if recur_every:
        result["repeats"] = describe_recurrence(recur_every)
    return result


async def list_my_reminders(ctx):
    rows = await db.get_user_reminders(ctx.message.author.id)
    return {
        "reminders": [
            {
                "id": row["code"],
                "message": row["message"],
                "when": discord_timestamp(row["remind_at"]),
                "remind_at_utc": _iso_utc(row["remind_at"]),
                "repeats": (
                    describe_recurrence(row["recur_every"]) if row["recur_every"] else None
                ),
            }
            for row in rows
        ]
    }


async def cancel_reminder(ctx, code):
    row = await _remind_cog(ctx).cancel_reminder_for_user(ctx.message.author.id, code)
    if row is None:
        return {"error": f"The user has no reminder with ID {normalize_reminder_code(code)}."}
    ctx.actions.append(f"cancelled reminder `{row['code']}`")
    return {"cancelled": row["code"], "message": row["message"]}


async def read_recent_messages(ctx, hours=None, count=None):
    channel = ctx.message.channel
    if not channel.permissions_for(ctx.message.author).read_message_history:
        return {"error": "The user can't read message history in this channel."}

    after = None
    if hours:
        after = datetime.now(timezone.utc) - timedelta(hours=int(hours))
        limit = MAX_RECAP_MESSAGES
    else:
        limit = max(1, min(int(count or 50), MAX_RECAP_MESSAGES))

    messages = await fetch_channel_messages(channel, ctx.bot.user, limit, after)
    return {
        "messages": [
            f"[{m['timestamp'][:16]}] {m['author']}: {m['content'][:MAX_RECAP_CHARS]}"
            for m in messages
        ]
    }


async def recall_person(ctx, name=None):
    memories = await recall(
        ctx.message.guild.id, name, ctx.message.author.id, memory_store
    )
    if not memories:
        return {"result": "nothing remembered"}
    return {
        "memories": [
            {
                "content": f"goes by {m['content']}" if m["kind"] == "name" else m["content"],
                "joke": m["joke"],
            }
            for m in memories
        ]
    }


async def show_my_memories(ctx):
    await send_aboutme(ctx.message.channel, ctx.message.author, memory_store)
    ctx.actions.append("showed memories")
    return {"result": "shown"}


async def link_name(ctx, name):
    if ctx.message.author.id != int(os.getenv("OWNER_ID", "0")):
        return {"error": "only the admin can link names"}
    target = next((u for u in ctx.message.mentions if not u.bot), None)
    if target is None:
        return {"error": "mention the user to link, e.g. @someone is ben"}
    try:
        status = await admin_link(
            ctx.message.guild.id, name, target.id, ctx.message.author.id, memory_store
        )
    except ValueError as e:
        return {"error": str(e)}
    return {"result": status}


HANDLERS = {
    "search_web": search_web,
    "search_notes": search_notes,
    "get_weather": get_weather,
    "set_reminder": set_reminder,
    "list_my_reminders": list_my_reminders,
    "cancel_reminder": cancel_reminder,
    "read_recent_messages": read_recent_messages,
    "recall_person": recall_person,
    "show_my_memories": show_my_memories,
    "link_name": link_name,
}


async def run_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    """Run a tool call. Errors go back to the model as {"error": ...} so it can recover."""
    logger.info("Tool call: %s(%s)", name, args)
    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return await handler(ctx, **(args or {}))
    except Exception as e:
        logger.exception("Tool %s failed", name)
        return {"error": str(e)}

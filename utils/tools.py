"""Functions the 'paruru,' chat model can call.

Caller identity (author, channel) always comes from ToolContext, never from model
arguments, so a user can only act on their own reminders and the current channel.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import discord

import db
from cogs.memories import send_aboutme
from utils.ai import (MODEL, _generate_with_error_retry, extract_response_text,
                      grounding_config, url_context_config)
from utils.links import extract_links
from utils.memory_rules import admin_link, recall
from utils.messages import fetch_channel_messages
from utils.notes import search_personal_notes
from utils.reminders import (describe_recurrence, discord_timestamp, next_occurrence,
                             normalize_reminder_code, parse_recurrence, parse_remind_time)
from utils.weather import fetch_weather

logger = logging.getLogger(__name__)

MAX_RECAP_MESSAGES = 200
MAX_RECAP_CHARS = 300
# How far back to look for the message that posted a link, and how long to wait
# for Discord to attach a preview to a just-posted one.
PREVIEW_HISTORY_LIMIT = 50
PREVIEW_RETRY_DELAY = 2

TOOL_GUIDANCE = (
    "\n\nYou can call tools. Use search_web for current events or facts you aren't sure of, "
    "read_url whenever someone asks about a link (what it says, is it true, summarize it): "
    "never guess a page's content from its URL or the chat around it. If read_url fails, "
    "say you couldn't open it. "
    "Use search_notes for anything that might be in your personal notes, get_weather for weather, "
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
        "name": "read_url",
        "description": "Open a web page and read what it actually says.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The full link, with https://."},
                "question": {
                    "type": "string",
                    "description": "Optional: what the user wants to know about the page.",
                },
            },
            "required": ["url"],
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


async def read_url(ctx, url, question=None):
    # Its own request for the same reason as search_web: url_context is a built-in
    # tool and can't share a request with function declarations on gemini-2.5.
    if not url.startswith(("http://", "https://")):
        return {"error": "Only http(s) links can be read."}
    prompt = f"Read this page: {url}\n\n" + (
        f"Answer from the page: {question}" if question
        else "Summarize what the page says."
    )
    response = await asyncio.to_thread(
        _generate_with_error_retry,
        model=MODEL,
        contents=prompt,
        config=url_context_config,
    )
    # Without a successful fetch the model only has the URL and would guess
    # from the slug, which is the behavior this tool exists to avoid.
    metadata = getattr(response.candidates[0], "url_context_metadata", None)
    statuses = [m.url_retrieval_status for m in (metadata.url_metadata or [])] if metadata else []
    text = extract_response_text(response)
    if "URL_RETRIEVAL_STATUS_SUCCESS" in statuses and text:
        return {"result": text}

    # Paywalls, logins and bot-blocking sites (x.com, etc.) often still give Discord
    # a preview card, since Discord fetches it as a crawler.
    preview = await _link_preview(ctx, url)
    if preview:
        return {
            "preview": preview,
            "note": (
                "Couldn't open the page itself; this is only Discord's link preview. "
                "Answer from it, say you only saw the preview, and don't fill in details "
                "it doesn't give."
            ),
        }
    return {"error": f"Couldn't open the page ({', '.join(map(str, statuses)) or 'not fetched'})."}


def _normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}"


def _posts_link(message, url) -> bool:
    # Normalized, since the model may drop tracking params from the link it was given.
    target = _normalize_url(url)
    return any(_normalize_url(link) == target for link in extract_links(message.content))


def _preview_from(message, url):
    """The embed for `url` in `message`, as the fields worth showing the model."""
    if not _posts_link(message, url):
        return None
    embeds = [e for e in message.embeds if e.title or e.description]
    target = _normalize_url(url)
    match = next((e for e in embeds if e.url and _normalize_url(e.url) == target), None)
    # Link fixers (vxtwitter, fixvx) give embeds a different URL than the one posted;
    # with a single link in the message its embed can only belong to that link.
    if match is None and len(extract_links(message.content)) == 1 and embeds:
        match = embeds[0]
    if match is None:
        return None
    fields = {
        "site": match.provider.name or match.author.name,
        "title": match.title,
        "description": match.description,
    }
    return {k: v for k, v in fields.items() if v}


async def _link_preview(ctx, url):
    """Discord's preview of `url`: from the asking message, the one it replies to,
    or a recent message that posted the link."""
    channel = ctx.message.channel
    try:
        current = await channel.fetch_message(ctx.message.id)
        # Discord attaches previews a moment after posting, so a link in the asking
        # message itself may not have its embed yet.
        if _posts_link(current, url) and not current.embeds:
            await asyncio.sleep(PREVIEW_RETRY_DELAY)
            current = await channel.fetch_message(ctx.message.id)
        if preview := _preview_from(current, url):
            return preview
        reference = ctx.message.reference
        if reference and reference.message_id:
            replied = await channel.fetch_message(reference.message_id)
            if preview := _preview_from(replied, url):
                return preview
        async for message in channel.history(limit=PREVIEW_HISTORY_LIMIT):
            if preview := _preview_from(message, url):
                return preview
    except discord.HTTPException as e:
        logger.warning("Could not look up a link preview: %s", e)
    return None


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

    def text(m):
        return f"goes by {m['content']}" if m["kind"] == "name" else m["content"]

    # Jokes go in their own list with an explicit note; a per-item flag alone
    # wasn't enough to stop the model stating them as fact.
    result = {"facts": [text(m) for m in memories if not m["joke"]]}
    jokes = [text(m) for m in memories if m["joke"]]
    if jokes:
        result["running_jokes"] = jokes
        result["note"] = (
            "running_jokes are bits people in the server made up. Play along and make it "
            "obvious it's a joke (e.g. 'apparently...', 'according to the group chat...'); "
            "never present them as true."
        )
    return result


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
    "read_url": read_url,
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

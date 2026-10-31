"""Turns a batch of channel messages into proposed memory ops with one model call."""

import asyncio
import json
import logging

from google.genai import types

from utils.ai import MODEL, _generate_with_error_retry, extract_response_text
from utils.memory_queue import QueuedMessage
from utils.memory_rules import Op

logger = logging.getLogger(__name__)

# Same model as chat.
LEARNER_MODEL = MODEL

LEARNER_INSTRUCTIONS = """You maintain a shared memory of the people in a Discord server.
You get recent messages and return changes to the memory as JSON ops. Most messages contain nothing worth remembering; returning no ops is normal.

What to remember
- Lasting facts about people (where they live, plans, jobs, games they play, likes and dislikes) and running jokes about them. Not passing chatter, opinions about the topic of the moment, or anything about the bot.
- Questions and requests are not facts, even when said to you: asking for recommendations or info ("what should i get for lunch in richmond", "cheap chinese food in richmond"), or a passing craving given as a request ("im feeling japanese"). Do not save what they asked for, where they asked about, or what they seem to want. Only remember something the person states as true about themselves or someone else ("i live in richmond", "i'm vegetarian").
- content is short, lowercase, third person, without the person's name: "going to japan in march", "hates cilantro".
- Never take facts from lines marked read-only context; use them only to understand the new messages.

Who it is about
- About the speaker ("i'm moving to osaka"): subject "self".
- About someone else: subject "name" with the name as written ("ben"). For an @mentioned person, use the name exactly as it appears after the @.
- Messages marked (to paruru) were said to you directly. Remember facts stated in them about any named person, even a name that isn't a known member; it starts a new profile. Most of these are questions or requests, which have nothing to remember.
- Only people in this server: linked names, server member names, or a name someone speaks to directly (then set addressed true). Drop celebrities, characters, and other outsiders.
- If a name could be a famous person, weigh in order: whether the name belongs to someone here; how it is written (a lowercase first name is a friend, a full name or surname is usually famous); whether that person is in the conversation; whether the claim is actually true of a well-known person. If still unsure, treat it as the server member.

Jokes
- Keep jokes and running bits, with joke true. Signals: laughing reactions or replies ("lmao", skull emoji), the person denying it, or a claim that is implausible next to what is already known.

Changing memory
- If something already remembered changed, use update with that memory's id. If it is no longer true, use forget. Never add a duplicate of an existing memory.
- link a name to a person when the conversation makes it clear who it is ("that's ben", "i'm ben", replying "ben" to someone). user_id must be an id shown in the message metadata.
- Every op needs the message_id of the new message that caused it.
"""

_STR = {"type": "STRING"}
OPS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "ops": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "op": {"type": "STRING", "enum": ["add", "update", "forget", "link"]},
                    "message_id": _STR,
                    "subject": {"type": "STRING", "enum": ["self", "name"]},
                    "name": _STR,
                    "content": _STR,
                    "joke": {"type": "BOOLEAN"},
                    "memory_id": _STR,
                    "user_id": _STR,
                    "addressed": {"type": "BOOLEAN"},
                },
                "required": ["op", "message_id"],
            },
        }
    },
    "required": ["ops"],
}


def _format_message(msg: QueuedMessage) -> str:
    to_paruru = "(to paruru) " if msg.direct else ""
    line = f"[{msg.id}] {to_paruru}{msg.author_label} ({msg.author_id}): {msg.content}"
    extras = []
    if msg.mention_ids:
        extras.append("mentions: " + ", ".join(str(i) for i in msg.mention_ids))
    if msg.reply_to_author_id is not None:
        extras.append(f"reply to: {msg.reply_to_author_id}")
    if msg.reactions:
        extras.append(
            "reactions: " + " ".join(f"{e}x{n}" for e, n in msg.reactions.items())
        )
    return " | ".join([line, *extras])


def _format_memory(memory: dict) -> str:
    about = memory["user_id"] if memory["user_id"] is not None else f"name {memory['name']}"
    tag = " (joke)" if memory.get("joke") else ""
    kind = "goes by " if memory["kind"] == "name" else ""
    return f"#{memory['id']} about {about}: {kind}{memory['content']}{tag}"


def build_learner_prompt(
    context: list[QueuedMessage],
    batch: list[QueuedMessage],
    member_names: list[str],
    links: dict[str, int],
    memories: list[dict],
) -> str:
    parts = [
        "Server member names: " + (", ".join(sorted(member_names)) or "(none)"),
        "Linked names: "
        + (", ".join(f"{n} -> {uid}" for n, uid in sorted(links.items())) or "(none)"),
        "Known memories:\n" + ("\n".join(_format_memory(m) for m in memories) or "(none)"),
    ]
    if context:
        parts.append(
            "EARLIER MESSAGES (read-only context, do not take facts from these):\n"
            + "\n".join(_format_message(m) for m in context)
        )
    parts.append("NEW MESSAGES:\n" + "\n".join(_format_message(m) for m in batch))
    return "\n\n".join(parts)


def _to_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


_REQUIRED = {
    "add": ("content",),
    "update": ("memory_id", "content"),
    "forget": ("memory_id",),
    "link": ("name", "user_id"),
}


def parse_ops(raw: str) -> list[Op]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Learner returned invalid JSON")
        return []
    entries = data.get("ops") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []

    ops = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("op") not in _REQUIRED:
            continue
        kind = entry["op"]
        message_id = _to_int(entry.get("message_id"))
        if message_id is None or any(not entry.get(f) for f in _REQUIRED[kind]):
            continue
        if kind == "add" and entry.get("subject") == "name" and not entry.get("name"):
            continue
        memory_id = _to_int(entry.get("memory_id"))
        user_id = _to_int(entry.get("user_id"))
        if (kind in ("update", "forget") and memory_id is None) or (
            kind == "link" and user_id is None
        ):
            continue
        ops.append(
            Op(
                op=kind,
                message_id=message_id,
                subject=entry.get("subject") or ("name" if entry.get("name") else "self"),
                name=entry.get("name"),
                content=entry.get("content"),
                joke=bool(entry.get("joke", False)),
                memory_id=memory_id,
                user_id=user_id,
                addressed=bool(entry.get("addressed", False)),
            )
        )
    return ops


async def extract_ops(prompt: str) -> list[Op]:
    """One learner call. API errors propagate so the caller keeps the batch pending."""
    response = await asyncio.to_thread(
        _generate_with_error_retry,
        model=LEARNER_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=LEARNER_INSTRUCTIONS,
            response_mime_type="application/json",
            response_schema=OPS_SCHEMA,
        ),
    )
    return parse_ops(extract_response_text(response) or "")

"""Rules for turning learner ops into memory changes.

The model proposes ops; everything here decides whether they are allowed. `store`
is the db module in production and a fake in tests.
"""

import logging
from dataclasses import dataclass
from typing import Callable

import db
from config import PARTNER_USER_ID
from utils.memory_queue import QueuedMessage

logger = logging.getLogger(__name__)

# Names permanently bound to one account; nobody else can be linked to them.
LOCKED_NAMES: dict[str, int] = {"sengakae": PARTNER_USER_ID}

MAX_NAME_LENGTH = 32
SELF_WORDS = {"me", "myself"}


def normalize_name(raw: str | None) -> str | None:
    if raw is None:
        return None
    name = raw.strip().lstrip("@").strip().casefold()
    if not name or "<@" in name or len(name) > MAX_NAME_LENGTH:
        return None
    return name


@dataclass
class Op:
    op: str
    message_id: int
    subject: str | None = None
    name: str | None = None
    content: str | None = None
    joke: bool = False
    memory_id: int | None = None
    user_id: int | None = None
    addressed: bool = False


@dataclass
class ApplyContext:
    guild_id: int
    messages: dict[int, QueuedMessage]
    member_names: dict[str, int]
    is_pending: Callable[[int], bool]


def _tied_to(user_id, msg, messages) -> bool:
    """The source message points at the user: they wrote it, it replies to or mentions
    them, or they replied to its author. Being in the room nearby is not enough."""
    if user_id in (msg.author_id, msg.reply_to_author_id) or user_id in msg.mention_ids:
        return True
    return any(m.author_id == user_id and m.reply_to_author_id == msg.author_id for m in messages)


async def _owned_memory(guild_id, memory_id, store):
    if memory_id is None:
        return None
    row = await store.get_memory(memory_id)
    if row is None or row["guild_id"] != guild_id:
        return None
    return row


async def _apply_add(op, msg, ctx, store) -> bool:
    content = (op.content or "").strip()
    if not content:
        return False
    if op.subject == "self":
        await store.add_memory(
            ctx.guild_id, msg.author_id, None, "fact", content, op.joke, msg.author_id, msg.id
        )
        return True

    name = normalize_name(op.name)
    if name is None:
        return False
    links = await store.get_links(ctx.guild_id)

    # An @mention is an exact account: link the name to it if the name is free,
    # and file the fact under that account either way.
    mentioned_id = msg.mention_names.get(name)
    if mentioned_id is not None:
        if name not in LOCKED_NAMES and name not in links:
            await store.link_name(ctx.guild_id, name, mentioned_id, msg.author_id, msg.id)
        await store.add_memory(
            ctx.guild_id, mentioned_id, name, "fact", content, op.joke, msg.author_id, msg.id
        )
        return True

    user_id = LOCKED_NAMES.get(name) or links.get(name)
    if user_id is None:
        addressed = op.addressed and name in msg.content.casefold()
        # Told straight to paruru: a new name starts its own profile.
        if name not in ctx.member_names and not addressed and not msg.direct:
            return False
    await store.add_memory(
        ctx.guild_id, user_id, name, "fact", content, op.joke, msg.author_id, msg.id
    )
    return True


async def _apply_update_or_forget(op, msg, ctx, store) -> bool:
    row = await _owned_memory(ctx.guild_id, op.memory_id, store)
    if row is None:
        return False
    owner_added = row["user_id"] is not None and row["added_by"] == row["user_id"]
    if owner_added and msg.author_id != row["user_id"]:
        return False

    if op.op == "update":
        content = (op.content or "").strip()
        if row["kind"] == "name" or not content:
            return False
        await store.update_memory(row["id"], content, op.joke)
        return True

    if row["kind"] == "name":
        if row["content"] in LOCKED_NAMES:
            return False
        await store.unlink_name(ctx.guild_id, row["content"])
    else:
        await store.delete_memory(row["id"])
    return True


async def _apply_link(op, msg, ctx, store) -> bool:
    name = normalize_name(op.name)
    if name is None or not _tied_to(op.user_id, msg, ctx.messages.values()):
        return False
    locked_id = LOCKED_NAMES.get(name)
    if locked_id is not None and op.user_id != locked_id:
        return False
    current = (await store.get_links(ctx.guild_id)).get(name)
    if current is not None:
        if current != op.user_id:
            logger.info("Rejected link %r -> %s: already linked to %s", name, op.user_id, current)
        return False
    await store.link_name(ctx.guild_id, name, op.user_id, msg.author_id, msg.id)
    return True


_APPLIERS = {
    "add": _apply_add,
    "update": _apply_update_or_forget,
    "forget": _apply_update_or_forget,
    "link": _apply_link,
}


async def apply_ops(ops: list[Op], ctx: ApplyContext, store=db) -> list[int]:
    """Apply allowed ops. Returns the source message ids of applied adds, updates and links."""
    react_ids = []
    for op in ops:
        msg = ctx.messages.get(op.message_id)
        applier = _APPLIERS.get(op.op)
        if msg is None or applier is None or not ctx.is_pending(op.message_id):
            continue
        try:
            applied = await applier(op, msg, ctx, store)
        except Exception:
            logger.exception("Failed to apply memory op %s", op)
            continue
        if applied and op.op != "forget" and op.message_id not in react_ids:
            react_ids.append(op.message_id)
    return react_ids


async def admin_link(guild_id: int, name: str, user_id: int, admin_id: int, store=db) -> str:
    """Admin link that may move a name from someone else. Raises ValueError if not allowed."""
    normalized = normalize_name(name)
    if normalized is None:
        raise ValueError(f"invalid name: {name!r}")
    locked_id = LOCKED_NAMES.get(normalized)
    if locked_id is not None and user_id != locked_id:
        raise ValueError(f"{normalized} is locked to another account")

    current = (await store.get_links(guild_id)).get(normalized)
    if current == user_id:
        return "unchanged"
    if current is not None:
        await store.unlink_name(guild_id, normalized)
    await store.link_name(guild_id, normalized, user_id, admin_id, None)
    return "linked" if current is None else "relinked"


async def remove_for_owner(guild_id: int, memory_id: int, owner_id: int, store=db) -> bool:
    row = await _owned_memory(guild_id, memory_id, store)
    if row is None or row["user_id"] != owner_id:
        return False
    if row["kind"] == "name":
        if row["content"] in LOCKED_NAMES:
            return False
        await store.unlink_name(guild_id, row["content"])
    else:
        await store.delete_memory(memory_id)
    return True


async def ensure_locked_links(guild_id: int, store=db) -> None:
    links = await store.get_links(guild_id)
    for name, user_id in LOCKED_NAMES.items():
        current = links.get(name)
        if current == user_id:
            continue
        if current is not None:
            await store.unlink_name(guild_id, name)
        await store.link_name(guild_id, name, user_id, user_id, None)


async def recall(guild_id: int, name: str | None, speaker_id: int, store=db) -> list[dict]:
    if name is None or name.strip().casefold() in SELF_WORDS:
        return await store.get_user_memories(guild_id, speaker_id)
    normalized = normalize_name(name)
    if normalized is None:
        return []
    user_id = LOCKED_NAMES.get(normalized) or (await store.get_links(guild_id)).get(normalized)
    if user_id is not None:
        return await store.get_user_memories(guild_id, user_id)
    return await store.get_name_memories(guild_id, normalized)

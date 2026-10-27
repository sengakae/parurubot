"""In-memory queue of guild messages waiting for the memory learner.

Each channel keeps its last MAX_PER_CHANNEL messages plus a marker for the last
processed one. Nothing here persists across restarts.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class QueuedMessage:
    id: int
    channel_id: int
    guild_id: int
    author_id: int
    author_label: str
    content: str
    mention_ids: list[int]
    reply_to_author_id: int | None
    created_at: datetime
    reactions: dict[str, int] = field(default_factory=dict)
    # casefolded display name / username of each mentioned user -> their ID
    mention_names: dict[str, int] = field(default_factory=dict)


@dataclass
class _Channel:
    guild_id: int
    messages: deque
    processed_up_to: int = 0


class PendingQueue:
    MAX_PER_CHANNEL = 100
    HOLDBACK = 3
    CONTEXT = 5

    def __init__(self):
        self._channels: dict[int, _Channel] = {}
        self._channel_of: dict[int, int] = {}

    def add(self, msg: QueuedMessage) -> None:
        channel = self._channels.get(msg.channel_id)
        if channel is None:
            channel = _Channel(msg.guild_id, deque(maxlen=self.MAX_PER_CHANNEL))
            self._channels[msg.channel_id] = channel
        if len(channel.messages) == channel.messages.maxlen:
            self._channel_of.pop(channel.messages[0].id, None)
        channel.messages.append(msg)
        self._channel_of[msg.id] = msg.channel_id

    def _find(self, message_id: int) -> QueuedMessage | None:
        channel_id = self._channel_of.get(message_id)
        if channel_id is None:
            return None
        for msg in self._channels[channel_id].messages:
            if msg.id == message_id:
                return msg
        return None

    def edit(self, message_id: int, content: str) -> None:
        msg = self._find(message_id)
        if msg is not None:
            msg.content = content

    def delete(self, message_id: int) -> None:
        msg = self._find(message_id)
        if msg is not None:
            self._channels[msg.channel_id].messages.remove(msg)
            del self._channel_of[message_id]

    def react(self, message_id: int, emoji: str, delta: int) -> None:
        msg = self._find(message_id)
        if msg is None:
            return
        count = msg.reactions.get(emoji, 0) + delta
        if count > 0:
            msg.reactions[emoji] = count
        else:
            msg.reactions.pop(emoji, None)

    def is_pending(self, message_id: int) -> bool:
        msg = self._find(message_id)
        if msg is None:
            return False
        return msg.id > self._channels[msg.channel_id].processed_up_to

    def _unprocessed(self, channel: _Channel) -> list[QueuedMessage]:
        return [m for m in channel.messages if m.id > channel.processed_up_to]

    def channels_with_pending(self, guild_id: int) -> list[int]:
        return [
            channel_id
            for channel_id, channel in self._channels.items()
            if channel.guild_id == guild_id
            and len(self._unprocessed(channel)) > self.HOLDBACK
        ]

    def take_batch(
        self, channel_id: int
    ) -> tuple[list[QueuedMessage], list[QueuedMessage]]:
        """(context, batch): batch is unprocessed minus the newest HOLDBACK; not marked."""
        channel = self._channels.get(channel_id)
        if channel is None:
            return [], []
        unprocessed = self._unprocessed(channel)
        batch = unprocessed[: max(0, len(unprocessed) - self.HOLDBACK)]
        processed = [m for m in channel.messages if m.id <= channel.processed_up_to]
        return processed[-self.CONTEXT :], batch

    def mark_processed(self, channel_id: int, up_to_message_id: int) -> None:
        channel = self._channels.get(channel_id)
        if channel is not None:
            channel.processed_up_to = max(channel.processed_up_to, up_to_message_id)

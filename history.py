import discord

from config import MAX_HISTORY

channel_history = {}


def add_message_to_history(
    channel_id: int, author_name: str, content: str, is_bot: bool = False
):
    """Add a message to the channel's history"""
    if channel_id not in channel_history:
        channel_history[channel_id] = []

    message_info = {
        "author": "Bot" if is_bot else author_name,
        "content": content,
        "timestamp": discord.utils.utcnow().isoformat(),
    }

    channel_history[channel_id].append(message_info)

    if len(channel_history[channel_id]) > MAX_HISTORY:
        channel_history[channel_id] = channel_history[channel_id][-MAX_HISTORY:]

    print(
        f"Added message to history for channel {channel_id}: {author_name} ({len(channel_history[channel_id])}/{MAX_HISTORY})"
    )

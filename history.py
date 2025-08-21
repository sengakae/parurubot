from collections import defaultdict, deque

from config import MAX_HISTORY

channel_history = defaultdict(lambda: deque(maxlen=MAX_HISTORY))

def add_message_to_history(channel_id, author, content, is_bot=False):
    """Add a message to the channel history with optional media content."""
    message_data = {
        'author': author,
        'content': content,
        'is_bot': is_bot
    }
    
    channel_history[channel_id].append(message_data)


def get_channel_history(channel_id, include_media=False):
    """Get the message history for a channel."""
    if channel_id not in channel_history:
        return []
    
    if include_media:
        return list(channel_history[channel_id])
    else:
        text_only = []
        for msg in channel_history[channel_id]:
            text_only.append({
                'author': msg['author'],
                'content': msg['content'],
                'is_bot': msg.get('is_bot', False)
            })
        return text_only
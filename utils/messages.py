from config import CHAR_LIMIT


def _avoid_mid_word_split(text: str, split_at: int) -> int:
    if split_at <= 0 or split_at >= len(text):
        return split_at

    if text[split_at - 1].isspace() or text[split_at].isspace():
        return split_at

    last_space = text.rfind(" ", 0, split_at)
    if last_space > 0:
        return last_space

    return split_at


def _find_split_point(text: str, limit: int) -> int:
    window = text[:limit]
    split_at = window.rfind("\n\n")
    if split_at < limit * 0.5:
        split_at = max(
            window.rfind(". "),
            window.rfind("! "),
            window.rfind("? "),
        )
        if split_at >= 0:
            split_at += 1
    if split_at < limit * 0.5:
        split_at = window.rfind("\n")
    if split_at < limit * 0.5:
        split_at = limit

    return _avoid_mid_word_split(text, split_at)


def split_message(text: str, limit: int = CHAR_LIMIT) -> list[str]:
    """Split text into chunks that fit Discord's message limit."""
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_at = _find_split_point(remaining, limit)
        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:limit]
            split_at = limit

        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()

    return chunks


async def fetch_channel_messages(channel, bot_user, limit, after=None):
    """The newest `limit` non-bot, non-empty messages (after `after`, if given), oldest first.

    Each message is a dict with author/content/timestamp.
    """
    messages = []
    # oldest_first=False so a busy window yields its newest messages, not its oldest.
    async for message in channel.history(limit=limit, after=after, oldest_first=False):
        if message.author != bot_user and message.content.strip():
            messages.append(
                {
                    "author": message.author.display_name,
                    "content": message.content,
                    "timestamp": message.created_at.isoformat(),
                }
            )
    messages.reverse()
    return messages

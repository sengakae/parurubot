from config import PARTNER_LABEL, PARTNER_USER_ID


def speaker_label(user) -> str:
    """Name the AI sees for a user. The partner is identified by ID, never by name."""
    if user.id == PARTNER_USER_ID:
        return PARTNER_LABEL
    name = user.display_name
    if "sengakae" in name.casefold():
        return f"{name} (not your boyfriend)"
    return name


def user_turn(author, content: str, replied=None) -> str:
    """The current message as the AI sees it, with the replied-to text when there is one."""
    header = f"message from {speaker_label(author)}"
    # getattr: a reply to a deleted message resolves to an object with no content.
    replied_text = (getattr(replied, "content", None) or "").strip()
    if replied_text:
        header += f', replying to {speaker_label(replied.author)}: "{replied_text}"'
    return f"[{header}]\n{content}"

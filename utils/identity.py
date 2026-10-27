from config import PARTNER_LABEL, PARTNER_USER_ID


def speaker_label(user) -> str:
    """Name the AI sees for a user. The partner is identified by ID, never by name."""
    if user.id == PARTNER_USER_ID:
        return PARTNER_LABEL
    name = user.display_name
    if "sengakae" in name.casefold():
        return f"{name} (not your boyfriend)"
    return name

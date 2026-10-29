from types import SimpleNamespace as NS

from utils.identity import user_turn

ANTO = NS(id=100, display_name="Anto")
BEN = NS(id=200, display_name="Ben")
LINK = "https://example.com/apple-ipad-ad"


def test_plain_message():
    assert user_turn(ANTO, "hi") == "[message from Anto]\nhi"


def test_reply_includes_replied_text():
    # The link lives in the replied message; without it the model never sees the URL.
    replied = NS(author=BEN, content=f"look at this {LINK}")
    turn = user_turn(ANTO, "what's this link talking about", replied)
    assert turn == (
        f"[message from Anto, replying to Ben: \"look at this {LINK}\"]\n"
        "what's this link talking about"
    )


def test_reply_to_empty_message_is_plain():
    replied = NS(author=BEN, content="  ")
    assert user_turn(ANTO, "what's this", replied) == "[message from Anto]\nwhat's this"

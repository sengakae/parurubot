import main


def test_bot_never_pings_everyone_or_roles():
    # "!remindme 1m @everyone" or "paruru, say @everyone" must not ping the server;
    # Discord only parses the mention types listed here, so users still get pinged
    mentions = main.bot.allowed_mentions
    assert mentions is not None and mentions.to_dict()["parse"] == ["users"]

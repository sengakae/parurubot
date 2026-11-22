import asyncio
from types import SimpleNamespace as NS

import discord
import pytest

import cogs.signup as signup


def test_change_is_saved_even_if_the_message_edit_fails(monkeypatch):
    # the edit used to come first, so a failed edit skipped the save and a restart lost the click
    saved = []

    async def save(view):
        saved.append(view)

    async def edit(**kwargs):
        raise discord.NotFound(NS(status=404, reason="Not Found"), "Unknown Message")

    monkeypatch.setattr(signup, "_save_signup", save)
    view = NS(build_embed=lambda: None)
    interaction = NS(response=NS(edit_message=edit))
    with pytest.raises(discord.NotFound):
        asyncio.run(signup._update_signup(interaction, view))
    assert saved == [view]

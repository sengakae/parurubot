import asyncio
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from config import CHAR_LIMIT, COMMAND_LIST, TIME_INDICATORS, WEB_SEARCH_KEYWORDS
from db import get_quote_by_key, init_db
from history import add_message_to_history, channel_history
from utils.ai import chat_with_ai, download_image_from_url
from utils.notes import load_personal_notes, search_personal_notes

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    """Called when the bot is ready and connected"""
    print(f"Logged in as {bot.user}")

    try:
        await init_db()
        print("DB connected successfully and pool initialized")
    except Exception as e:
        print(f"Failed to connect to DB: {e}")
        import sys

        sys.exit(1)

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, load_personal_notes)

    server_count = len(bot.guilds)
    for server in bot.guilds:
        print(f"- {server.id} (name: {server.name})")

    print(f"ParuruBot is in {server_count} server(s).")


@bot.event
async def on_message(message):
    """Handle incoming messages"""
    if message.author == bot.user:
        return

    message_content = message.content
    if message.attachments:
        image_count = sum(1 for att in message.attachments
                          if any(att.filename.lower().endswith(ext)
                                 for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']))
        if image_count > 0:
            message_content += f" [sent {image_count} image(s)]"

    if not message.content.startswith("!"):
        add_message_to_history(
            message.channel.id, message.author.display_name, message_content
        )

    content = message.content.split()

    if not content:
        return

    if content[0].startswith("!"):
        keyword = content[0][1:]
        if keyword in COMMAND_LIST:
            await bot.process_commands(message)
            return
        else:
            quote = await get_quote_by_key(keyword)
            if quote:
                await message.channel.send(quote)
            return

    if message.content.lower().startswith("paruru, "):
        await handle_ai_chat(message)
        return


async def handle_ai_chat(message):
    """Handle AI chat messages starting with 'paruru, '"""
    cleaned_content = message.content[8:].strip()

    try:
        channel_id = message.channel.id
        history_context = ""
        if channel_id in channel_history and channel_history[channel_id]:
            history_context = "\n\nRecent conversation context (last 20 messages):\n"
            for msg in channel_history[channel_id]:
                history_context += f"{msg['author']}: {msg['content']}\n"
            history_context += f"\nCurrent message: {cleaned_content}"
            print(f"Using {len(channel_history[channel_id])} messages of context")

        relevant_notes = search_personal_notes(cleaned_content, n_results=2)
        notes_context = ""
        if relevant_notes:
            notes_context = (
                f"\n\nRelevant information from your personal notes:\n{relevant_notes}"
            )
            print("Found relevant notes for this query")

        images = []
        if message.attachments:
            print(f"Found {len(message.attachments)} attachments")
            for attachment in message.attachments:
                if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                    print(f"Processing image: {attachment.filename}")
                    image = await download_image_from_url(attachment.url)
                    if image:
                        images.append(image)
                        if not cleaned_content.strip():
                            cleaned_content = "What do you see in this image?"

        needs_web_search = any(
            keyword in cleaned_content.lower() for keyword in WEB_SEARCH_KEYWORDS
        ) or any(indicator in cleaned_content.lower() for indicator in TIME_INDICATORS)

        response_text = chat_with_ai(
            cleaned_content, history_context, notes_context, needs_web_search, images
        )

        print(f"Response: {response_text}")
        add_message_to_history(message.channel.id, "Bot", response_text, is_bot=True)

        if len(response_text) > CHAR_LIMIT:
            await message.channel.send("whoa that's way too much text, my brain hurts!")
        else:
            await message.channel.send(response_text)

    except Exception as e:
        print(f"Error generating response: {e}")
        await message.channel.send("oops, something broke, gimme a sec...")


async def load_cogs():
    cog_dir = Path("cogs")
    loaded_cogs = 0
    failed_cogs = 0

    for cog_file in cog_dir.glob("*.py"):
        if cog_file.name == "__init__.py":
            continue

        extension_name = f"cogs.{cog_file.stem}"
        try:
            await bot.load_extension(extension_name)
            print(f"Loaded {extension_name}")
            loaded_cogs += 1
        except Exception as e:
            print(f"Failed to load {extension_name}: {e}")
            failed_cogs += 1

    print(f"\nCog loading complete: {loaded_cogs} loaded, {failed_cogs} failed")
    if loaded_cogs > 0:
        print(f"Active cogs: {', '.join(bot.cogs.keys())}")


async def main():
    async with bot:
        await load_cogs()
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("ParuruBot killed by user.")

import asyncio
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from config import CHAR_LIMIT, COMMAND_LIST, TIME_INDICATORS, WEB_SEARCH_KEYWORDS
from db import get_quote_by_key, init_db
from history import add_message_to_history, get_channel_history
from utils.ai import chat_with_ai, convert_pil_to_part, convert_video_to_part
from utils.links import collect_images_from_message, extract_youtube_urls
from utils.notes import load_personal_notes, search_personal_notes

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

MAX_IMAGES = 10
MAX_VIDEOS = 5

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
    
    content = message.content.split()
    if not content and not message.attachments:
        return
    
    if content:
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
            
        if content[0].lower() == ("paruru,"):
            await handle_ai_chat(message)
            return

    message_images = await collect_images_from_message(message)
    youtube_urls = extract_youtube_urls(message.content)

    message_content = message.content
    if message_images:
        message_content += f" [sent {len(message_images)} image(s)]"
    if youtube_urls:
        message_content += f" [shared {len(youtube_urls)} YouTube video(s)]"

    if not message.content.startswith("!"):
        add_message_to_history(
            message.channel.id, 
            message.author.display_name, 
            message_content, 
            is_bot=False,
            images=message_images,
            videos=youtube_urls
        )


async def handle_ai_chat(message):
    """Handle AI chat messages starting with 'paruru, '"""
    cleaned_content = message.content[8:].strip()

    try:
        channel_id = message.channel.id

        current_images  = []
        current_videos = []

        """Check current message"""
        urls, stripped_text = extract_youtube_urls(cleaned_content)
        current_videos.extend(urls)
        current_images.extend(await collect_images_from_message(stripped_text))

        """Check replied message"""
        if message.reference and message.reference.resolved:
            replied_msg = message.reference.resolved or await message.channel.fetch_message(message.reference.message_id)
            urls, stripped_text = extract_youtube_urls(replied_msg.content)
            current_videos.extend(urls)    
            current_images.extend(await collect_images_from_message(stripped_text, replied_msg.attachments))

        if current_images:
            print(f"Found {len(current_images)} images")

        if current_videos:
            print(f"Found {len(current_videos)} YouTube URLs: {current_videos}")

        if len(current_images) > MAX_IMAGES:
            print(f"Limiting images from {len(current_images)} to {MAX_IMAGES}")
            current_images = current_images[:MAX_IMAGES - len(current_images)]
            
        if len(current_videos) > MAX_VIDEOS:
            print(f"Limiting YouTube URLs from {len(current_videos)} to {MAX_VIDEOS}")
            current_videos = current_videos[:MAX_VIDEOS - len(current_videos)]
        
        history_messages = get_channel_history(channel_id, include_media=True)
        history_context = ""

        if history_messages:
            conversation_parts = [{"text": "Here's the recent conversation context:"}]
            added_media = bool(current_images or current_videos)

            for msg in history_messages:
                if msg["has_media"]:
                    if added_media:
                        continue

                    conversation_parts.append({"text": f"{msg['author']} shared media:"})

                    last_media_index = next(
                        (i for i in range(len(history_messages)-1, -1, -1) if history_messages[i]["has_media"]),
                        None
                    )

                    for i, msg in enumerate(history_messages):
                        if msg["has_media"]:
                            if i != last_media_index:
                                continue

                            conversation_parts.append({"text": f"{msg['author']} shared media:"})

                            for img in msg["images"]:
                                conversation_parts.append(convert_pil_to_part(img)) 

                            for vid in msg["videos"]:
                                conversation_parts.append(convert_video_to_part(vid)) 

                        else:
                            conversation_parts.append({"text": f"{msg['author']}: {msg['content']}"})

            history_context = {
                "role": "user",
                "parts": conversation_parts
            }

        relevant_notes = search_personal_notes(cleaned_content, n_results=2)
        notes_context = ""
        if relevant_notes:
            notes_context = (
                f"\n\nRelevant information from your personal notes:\n{relevant_notes}"
            )
            print("Found relevant notes for this query")

        needs_web_search = any(
            keyword in cleaned_content.lower() for keyword in WEB_SEARCH_KEYWORDS
        ) or any(indicator in cleaned_content.lower() for indicator in TIME_INDICATORS) or len(current_videos)

        async with message.channel.typing():
            response_text = chat_with_ai(
                cleaned_content, history_context, notes_context, needs_web_search, current_images, current_videos
            )

        print(f"Response: {response_text}")
        add_message_to_history(message.channel.id, "Bot", response_text, is_bot=True, images=current_images, videos=current_videos)

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

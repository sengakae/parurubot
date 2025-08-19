import asyncio
import os
import re
from pathlib import Path

import chromadb
import discord
import google.generativeai as genai
import pandas as pd
import requests
from discord.ext import commands
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

# Uses local config.py
from config import (
    CHAR_LIMIT,
    COMMAND_LIST,
    MAX_HISTORY,
    SYSTEM_PROMPT,
    TIME_INDICATORS,
    WEB_SEARCH_KEYWORDS,
)
from db import (
    add_quote,
    get_all_keys,
    get_quote_by_key,
    get_random_quote,
    init_db,
    remove_quote,
)

# SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT")
# CHAR_LIMIT = int(os.getenv("CHAR_LIMIT", 2000))
# MAX_HISTORY = int(os.getenv("MAX_HISTORY", 20))
# WEB_SEARCH_KEYWORDS = json.loads(os.getenv("WEB_SEARCH_KEYWORDS", '[]'))
# TIME_INDICATORS = json.loads(os.getenv("TIME_INDICATORS", '[]'))
# COMMAND_LIST = json.loads(os.getenv("COMMAND_LIST", '[]'))

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WEATHER_TOKEN = os.getenv("WEATHER_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-2.0-flash")
embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
chroma_client = chromadb.PersistentClient(path="./chroma_db")

channel_history = {}

try:
    collection = chroma_client.get_collection("personal_notes")
    print("Loaded existing notes database")
except:
    collection = chroma_client.create_collection("personal_notes")
    print("Created new notes database")


def load_personal_notes():
    """Load all .txt files from a 'notes' folder into the vector database"""
    notes_folder = Path("./notes")
    if not notes_folder.exists():
        print("No notes folder found - create ./notes/ and add .txt files")
        return
    
    print("Looking for TXT files...")
    for note_file in notes_folder.rglob("*.txt"):
        print("Found TXT:", note_file)
        try:
            with open(note_file, 'r', encoding='utf-8') as file:
                content = file.read()
            
            chunks = [content[i:i+1000] for i in range(0, len(content), 800)]
            
            for i, chunk in enumerate(chunks):
                relative_path = note_file.relative_to(notes_folder)
                doc_id = f"{note_file.stem}_{i}"
                collection.upsert(
                    documents=[chunk],
                    ids=[doc_id],
                    metadatas=[{"source": str(relative_path), "chunk": i}]
                )
            print(f"Loaded {relative_path} ({len(chunks)} chunks)")
        except Exception as e:
            print(f"Error loading {note_file.name}: {e}")

    print("Looking for CSV files...")
    for csv_file in notes_folder.rglob("*.csv"):
        print("Found CSV:", csv_file)
        try:
            df = pd.read_csv(csv_file)
            
            content_chunks = []
            
            summary = f"CSV Summary - File: {csv_file.name}, Columns: {', '.join(df.columns)}, Total rows: {len(df)}"
            content_chunks.append(summary)
            
            for idx, row in df.iterrows():
                row_items = []
                for col, val in row.items():
                    if pd.notna(val) and str(val).strip():
                        row_items.append(f"{col}: {val}")
                
                if row_items:
                    row_text = f"Entry {idx + 1} - " + ", ".join(row_items)
                    content_chunks.append(row_text)
            
            grouped_chunks = []
            for i in range(0, len(content_chunks), 8):
                chunk = "\n".join(content_chunks[i:i+8])
                grouped_chunks.append(chunk)
            
            relative_path = csv_file.relative_to(notes_folder)
            for i, chunk in enumerate(grouped_chunks):
                doc_id = f"{relative_path.stem}_csv_{i}"
                collection.upsert(
                    documents=[chunk],
                    ids=[doc_id],
                    metadatas=[{"source": str(relative_path), "type": "csv", "chunk": i, "total_rows": len(df)}]
                )
            
            print(f"Loaded CSV {relative_path} ({len(df)} rows → {len(grouped_chunks)} chunks)")
            
        except Exception as e:
            print(f"Error loading CSV {csv_file}: {e}")


def search_personal_notes(query, n_results=3):
    """Search personal notes for relevant information"""
    try:
        results = collection.query(
            query_texts=[query],
            n_results=n_results
        )
        
        if results['documents'] and results['documents'][0]:
            relevant_info = []
            for doc, metadata in zip(results['documents'][0], results['metadatas'][0]):
                source_file = metadata['source']
                file_type = metadata.get('type', 'unknown')
                
                if file_type == 'csv':
                    preview = doc[:400] + "..." if len(doc) > 400 else doc
                    relevant_info.append(f"From CSV {source_file}:\n{preview}")
                else:
                    preview = doc[:300] + "..." if len(doc) > 300 else doc
                    relevant_info.append(f"From {source_file}: {preview}")
            return "\n\n".join(relevant_info)
        return None
    except:
        return None


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
        f"Added message to history for channel {channel_id}: {author_name} ({len(channel_history[channel_id])}/20)"
    )


weather_url = "http://api.openweathermap.org/data/2.5/weather?"

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
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


@bot.command(name="purge", help="purges the last [number] lines (max: 100)")
async def purge(ctx, number=10):
    lines = int(number)
    await ctx.channel.purge(limit=lines)


@bot.command(name="weather", help="prints weather information of city")
async def weather(ctx, *, city: str):
    url = f"{weather_url}appid={WEATHER_TOKEN}&q={city}"
    try:
        response = requests.get(url)
        data = response.json()

        channel = ctx.message.channel
        if data.get("cod") == 200:
            async with channel.typing():
                weather = data["main"]
                current_temp = str(round(weather["temp"] - 273.15))
                current_humidity = weather["humidity"]
                description = data["weather"][0]
                country = data["sys"]["country"]
                description_main = description["main"]
                description_info = description["description"]

                embed = discord.Embed(
                    title=f"Weather in {city.capitalize()}, {country}",
                    color=ctx.guild.me.top_role.color,
                    timestamp=ctx.message.created_at,
                )
                embed.add_field(
                    name="Description",
                    value=f"**{description_main} - {description_info}**",
                    inline=False,
                )
                embed.add_field(
                    name="Temperature", value=f"**{current_temp}°C**", inline=False
                )
                embed.add_field(
                    name="Humidity", value=f"**{current_humidity}%**", inline=False
                )
                embed.set_footer(text=f"Requested by {ctx.author.display_name}")

                await ctx.send(embed=embed)
        else:
            await ctx.send("City not found.")
    except:
        await ctx.send("An error occurred while fetching weather info.")


@bot.command(name="gs", help="calculates e7 gear score")
async def gs(ctx, *, values):
    stats = values.split()
    gear_score = 0
    for stat in stats:
        if isinstance(stat, str):
            num_val = int(re.search(r"\d+", stat).group())
            if "cc" in stat:
                gear_score += num_val * 1.6
            elif "cd" in stat:
                gear_score += num_val * 1.14
            elif "s" in stat:
                gear_score += num_val * 2
            elif "atk" in stat:
                gear_score += num_val * 3.46 / 39
            elif "def" in stat:
                gear_score += num_val * 4.99 / 31
            elif "hp" in stat:
                gear_score += num_val * 3.09 / 174
            else:
                gear_score += int(stat)

    await ctx.send(f"Gear score: {round(gear_score, 2)}")


@bot.command(name="history", help="show recent conversation history")
async def history(ctx):
    channel_id = ctx.channel.id
    if channel_id in channel_history and channel_history[channel_id]:
        history_text = f"**Recent conversation history ({len(channel_history[channel_id])}/20 messages):**\n"
        for i, msg in enumerate(channel_history[channel_id][-10:], 1):
            history_text += f"{i}. **{msg['author']}**: {msg['content'][:100]}{'...' if len(msg['content']) > 100 else ''}\n"
        await ctx.send(history_text)
    else:
        await ctx.send("No conversation history found for this channel.")


@bot.command(
    name="fullhistory", help="show full conversation history (up to 20 messages)"
)
async def fullhistory(ctx):
    channel_id = ctx.channel.id
    if channel_id in channel_history and channel_history[channel_id]:
        history_text = f"**Full conversation history ({len(channel_history[channel_id])}/20 messages):**\n"
        for i, msg in enumerate(channel_history[channel_id], 1):
            history_text += f"{i}. **{msg['author']}**: {msg['content']}\n"
        await ctx.send(history_text)
    else:
        await ctx.send("No conversation history found for this channel.")


@bot.command(name="summary", help="generate AI summary of last 500 messages in channel")
async def summary(ctx):
    try:
        if not ctx.channel.permissions_for(ctx.author).read_message_history:
            await ctx.send(
                "you don't have permission to read message history in this channel"
            )
            return

        await ctx.send("Analyzing the last 500 messages... this might take a moment")

        messages = []
        message_count = 0
        async for message in ctx.channel.history(limit=500):
            if message.author != bot.user and message.content.strip():
                messages.append(
                    {
                        "author": message.author.display_name,
                        "content": message.content,
                        "timestamp": message.created_at.isoformat(),
                    }
                )
                message_count += 1

        if not messages:
            await ctx.send("no messages found to summarize")
            return

        total_content_length = sum(len(msg["content"]) for msg in messages)
        if total_content_length > 30000:
            await ctx.send(
                "Conversation is too long to analyze completely. summarizing the most recent messages..."
            )
            messages = messages[-200:]

        conversation_text = (
            f"Here are the last {len(messages)} messages from this Discord channel:\n\n"
        )
        for msg in messages:
            conversation_text += f"{msg['author']}: {msg['content']}\n"

        summary_prompt = f"Please analyze this Discord conversation and provide a concise summary of the main topics discussed. Focus on:\n- Key themes and subjects\n- Important questions or decisions made\n- Any ongoing discussions or unresolved topics\n- General mood/tone of the conversation\n\nKeep the summary under 800 characters and organize it as a clear, structured list. Here's the conversation:\n\n{conversation_text}"

        response = model.generate_content(summary_prompt)

        if len(response.text) > 800:
            response.text = response.text[:797] + "..."

        await ctx.send(
            f"**Channel Summary ({len(messages)} messages analyzed):**\n{response.text}"
        )

    except Exception as e:
        print(f"Error generating summary: {e}")
        await ctx.send("oops, something went wrong while generating the summary")


@bot.command(name="add", help="Save a quote with a keyword")
async def add(ctx, keyword: str, *, quote: str):
    await add_quote(keyword, quote)
    await ctx.send(f"Quote '{keyword}' added successfully!")


@bot.command(name="rm", help="Remove a quote by keyword")
async def rm(ctx, keyword: str):
    removed = await remove_quote(keyword)
    if removed:
        await ctx.send(f"Quote '{keyword}' removed successfully!")
    else:
        await ctx.send(f"No quote found for keyword '{keyword}'.")


@bot.command(name="showquotes", help="List all quote keywords")
async def showquotes(ctx):
    keywords = await get_all_keys()
    if keywords:
        output = "```plaintext\n" + "\n".join(keywords) + "\n```"
        await ctx.send(f"All quote keywords:\n{output}")
    else:
        await ctx.send("No quotes found in the database.")
        

@bot.command(name="rquote", help="Print a random saved quote")
async def rquote(ctx):
    quote_row = await get_random_quote()
    if quote_row:
        await ctx.send(f"**{quote_row['key']}**: {quote_row['value']}")
    else:
        await ctx.send("No quotes found in the database.")


@bot.command(name="quote", help="Show a quote by its keyword")
async def quote(ctx, keyword: str):
    value = await get_quote_by_key(keyword)
    if value:
        await ctx.send(f"**{keyword}**: {value}")
    else:
        await ctx.send(f"No quote found for keyword '{keyword}'.")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if not message.content.startswith("!"):
        add_message_to_history(message.channel.id, message.author.display_name, message.content)

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
        cleaned_content = message.content[8:].strip()
        try:
            channel_id = message.channel.id
            history_context = ""
            if channel_id in channel_history and channel_history[channel_id]:
                history_context = (
                    "\n\nRecent conversation context (last 20 messages):\n"
                )
                for msg in channel_history[channel_id]:
                    history_context += f"{msg['author']}: {msg['content']}\n"
                history_context += f"\nCurrent message: {cleaned_content}"
                print(f"Using {len(channel_history[channel_id])} messages of context")

            relevant_notes = search_personal_notes(cleaned_content, n_results=2)
            notes_context = ""
            if relevant_notes:
                notes_context = f"\n\nRelevant information from your personal notes:\n{relevant_notes}"
                print("Found relevant notes for this query")

            needs_web_search = any(keyword in cleaned_content.lower() for keyword in WEB_SEARCH_KEYWORDS) or \
                               any(indicator in cleaned_content.lower() for indicator in TIME_INDICATORS)

            if needs_web_search:
                print("Using web search model for current information")
                search_prompt = f"{SYSTEM_PROMPT}{notes_context}\n\nNow, please provide current and up-to-date information about: {cleaned_content}. Use your knowledge to give the most recent and accurate information available. Keep your response concise and under {CHAR_LIMIT} characters while maintaining your casual, friendly personality."
                if history_context:
                    search_prompt += (
                        f"\n\nContext from recent conversation:\n{history_context}"
                    )
                response = model.generate_content(search_prompt)

                if len(response.text) > CHAR_LIMIT:
                    print(
                        f"Response too long ({len(response.text)} chars), truncating to {CHAR_LIMIT}"
                    )
                    truncated = response.text[: CHAR_LIMIT - 3]
                    last_period = truncated.rfind(".")
                    last_exclamation = truncated.rfind("!")
                    last_question = truncated.rfind("?")

                    break_point = max(last_period, last_exclamation, last_question)
                    if break_point > CHAR_LIMIT * 0.8:
                        response.text = truncated[: break_point + 1]
                    else:
                        response.text = truncated + "..."
            else:
                print("Using regular model for conversation")
                conversation = [
                    {"role": "user", "parts": [f"{SYSTEM_PROMPT}{notes_context}"]},
                    {
                        "role": "model",
                        "parts": ["got it! i'll be casual and friendly in our chats"],
                    },
                ]

                if history_context:
                    conversation.append(
                        {
                            "role": "user",
                            "parts": [
                                f"Here's the recent conversation context:\n{history_context}"
                            ],
                        }
                    )

                conversation.append({"role": "user", "parts": [cleaned_content]})
                response = model.generate_content(conversation)

            print(f"Response: {response.text}")
            add_message_to_history(message.channel.id, "Bot", response.text, is_bot=True)

            if len(response.text) > CHAR_LIMIT:
                await message.channel.send("whoa that's way too much text, my brain hurts!")
            else:
                await message.channel.send(response.text)
        except Exception as e:
            print(f"Error generating response: {e}")
            await message.channel.send("Oops, something broke, gimme a sec...")
        return

bot.run(DISCORD_TOKEN)

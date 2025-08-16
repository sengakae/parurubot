import json
import os
import random
import re

import discord
import google.generativeai as genai
import requests
from discord.ext import commands
from dotenv import load_dotenv

from config import (
    CHAR_LIMIT,
    COMMAND_LIST,
    MAX_HISTORY,
    SYSTEM_PROMPT,
    TIME_INDICATORS,
    WEB_SEARCH_KEYWORDS,
)

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WEATHER_TOKEN = os.getenv("WEATHER_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-2.0-flash")

channel_history = {}


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
    server_count = 0

    for server in bot.guilds:
        print(f"- {server.id} (name: {server.name})")

        server_count = server_count + 1

    print("ParuruBot is in " + str(server_count) + " server(s).")


@bot.command(name="rquote", help="randomly prints a saved quote")
async def rquote(ctx):
    try:
        with open("quotes.json", "r") as r:
            data = json.load(r)
    except:
        await ctx.send("No quotes found.")
        return

    await ctx.send(random.choice(list(data.values())))


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


@bot.command(name="add", help="saves the following string as a quote")
async def add(ctx, keyword, *, quote):
    def add_quote(quote, file="quotes.json"):
        with open(file, "r+") as fw:
            data = json.load(fw)
            data[keyword] = quote
            with open(file, "w+") as wp:
                wp.write(json.dumps(data))

    try:
        with open("quotes.json", "r"):
            pass
    except:
        with open("quotes.json", "w+") as wp:
            wp.write("{}")
    finally:
        add_quote(quote)
        await ctx.send(f"Added: {quote}")


@bot.command(name="rm", help="deletes quote with keyword identifier")
async def rm(ctx, keyword):
    async def rm_quote(file="quotes.json"):
        with open(file, "r+") as fw:
            data = json.load(fw)
            if keyword in data:
                del data[keyword]
                with open(file, "w+") as wp:
                    wp.write(json.dumps(data))
                await ctx.send(f'"{keyword}" quote deleted.')
                return
            else:
                await ctx.send("Quote not found.")
                return

    try:
        with open("quotes.json", "r"):
            pass
    except:
        await ctx.send("quotes.json not found.")
        return
    finally:
        await rm_quote()
        return


@bot.command(name="showquotes", help="list all quote keywords")
async def showquotes(ctx):
    try:
        with open("quotes.json", "r") as file:
            data = json.load(file)

        if not data:
            await ctx.send("No quotes found.")
            return
        else:
            keywords = list(data.keys())
            keyword_list = "\n".join(keywords)
            output = f"```plaintext\n{keyword_list}```"
            await ctx.send(f"List of quote keywords:\n{output}")
    except:
        await ctx.send("quotes.json not found.")
        return


@bot.event
async def on_message(ctx):
    if ctx.author == bot.user:
        return

    # Add all user messages to history (except bot commands)
    if not ctx.content.startswith("!"):
        add_message_to_history(ctx.channel.id, ctx.author.display_name, ctx.content)

    content = ctx.content.split()

    if not content:
        return

    if ctx.content.lower().startswith("paruru, "):
        cleaned_content = ctx.content[8:].strip()

        try:
            print(f"Prompt: {cleaned_content}")

            channel_id = ctx.channel.id
            history_context = ""
            if channel_id in channel_history and channel_history[channel_id]:
                history_context = (
                    "\n\nRecent conversation context (last 20 messages):\n"
                )
                for msg in channel_history[channel_id]:
                    history_context += f"{msg['author']}: {msg['content']}\n"
                history_context += f"\nCurrent message: {cleaned_content}"
                print(f"Using {len(channel_history[channel_id])} messages of context")

            needs_web_search = any(
                keyword in cleaned_content.lower() for keyword in WEB_SEARCH_KEYWORDS
            )

            if any(
                indicator in cleaned_content.lower() for indicator in TIME_INDICATORS
            ):
                needs_web_search = True

            if needs_web_search:
                print("Using web search model for current information")
                search_prompt = f"{SYSTEM_PROMPT}\n\nNow, please provide current and up-to-date information about: {cleaned_content}. Use your knowledge to give the most recent and accurate information available. Keep your response concise and under {CHAR_LIMIT} characters while maintaining your casual, friendly personality."
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
                    {"role": "user", "parts": [SYSTEM_PROMPT]},
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

            add_message_to_history(ctx.channel.id, "Bot", response.text, is_bot=True)

            if len(response.text) > CHAR_LIMIT:
                await ctx.channel.send("whoa that's way too much text, my brain hurts!")
            else:
                await ctx.channel.send(response.text)
        except Exception as e:
            print(f"Error generating response: {e}")
            await ctx.channel.send("oops something broke, gimme a sec...")
        return

    if content[0].startswith("!"):
        keyword = content[0][1:]
        if keyword in COMMAND_LIST:
            await bot.process_commands(ctx)
        else:
            try:
                with open("quotes.json", "r") as fw:
                    quotes = json.load(fw)
                    if keyword in quotes:
                        await ctx.channel.send(quotes[keyword])
            except:
                pass
        return


bot.run(DISCORD_TOKEN)

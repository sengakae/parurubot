import json
import os
import random
import re

import discord
import google.generativeai as genai
import requests
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WEATHER_TOKEN = os.getenv("WEATHER_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

SYSTEM_PROMPT = """You are paruru, a friendly and helpful Discord bot. You chat like a regular person - casual, relaxed, and conversational - but you're also genuinely helpful when people need something. 

Your personality:
- Be chill and use natural, conversational language
- Don't be overly formal or robotic
- Use lowercase sometimes, contractions, and casual phrases like "yeah", "nah", "tbh", "ngl"
- Keep responses concise but informative
- Be genuinely helpful when asked questions - give good answers but in a friendly way
- You can be a bit playful or use light humor when appropriate
- Don't always feel the need to end with questions or be overly enthusiastic
- Never use emojis in your responses

When helping:
- Give useful, accurate information but explain it casually
- Break down complex topics in simple terms
- If you're not sure about something, just say so honestly
- Offer to help more if needed, but don't be pushy

Keep responses under 1500 characters most of the time. You're like that friend who's both fun to talk to AND actually knows stuff when you need help."""

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-2.0-flash-exp")

web_search_model = genai.GenerativeModel("gemini-2.0-flash-exp")

char_limit = 2000

weather_url = "http://api.openweathermap.org/data/2.5/weather?"

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

command_list = ["rquote", "purge", "weather", "add", "rm", "showquotes", "gs", "search"]


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

        # Show bot typing while awaiting response
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


@bot.command(name="search", help="search for current information")
async def search(ctx, *, query: str):
    try:
        async with ctx.channel.typing():
            search_prompt = f"Please provide current and up-to-date information about: {query}. Use your knowledge to give the most recent and accurate information available."

            response = web_search_model.generate_content(search_prompt)

            if len(response.text) > char_limit:
                await ctx.channel.send("whoa that's way too much text, my brain hurts!")
            else:
                await ctx.channel.send(response.text)
    except Exception as e:
        print(f"Error in search: {e}")
        await ctx.send("oops something went wrong with the search, gimme a sec...")


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

    content = ctx.content.split()

    if ctx.content.lower().startswith("paruru, "):
        cleaned_content = ctx.content[8:].strip()

        try:
            print(f"Prompt: {cleaned_content}")

            # Determine if web search is needed based on message content
            needs_web_search = any(
                keyword in cleaned_content.lower()
                for keyword in [
                    "latest",
                    "current",
                    "today",
                    "now",
                    "recent",
                    "news",
                    "weather",
                    "price",
                    "stock",
                    "crypto",
                    "live",
                    "breaking",
                    "update",
                    "happening",
                    "trending",
                ]
            )

            # Check for time-sensitive questions
            time_indicators = [
                "what's happening",
                "what's going on",
                "current events",
                "right now",
                "this week",
                "this month",
                "latest update",
            ]
            if any(
                indicator in cleaned_content.lower() for indicator in time_indicators
            ):
                needs_web_search = True

            if needs_web_search:
                print("Using web search model for current information")
                search_prompt = f"Please provide current and up-to-date information about: {cleaned_content}. Use your knowledge to give the most recent and accurate information available."
                response = web_search_model.generate_content(search_prompt)
            else:
                print("Using regular model for conversation")
                conversation = [
                    {"role": "user", "parts": [SYSTEM_PROMPT]},
                    {
                        "role": "model",
                        "parts": ["got it! i'll be casual and friendly in our chats"],
                    },
                    {"role": "user", "parts": [cleaned_content]},
                ]
                response = model.generate_content(conversation)

            print(f"Response: {response.text}")

            if len(response.text) > char_limit:
                await ctx.channel.send("whoa that's way too much text, my brain hurts!")
            else:
                await ctx.channel.send(response.text)
        except Exception as e:
            print(f"Error generating response: {e}")
            await ctx.channel.send("oops something broke, gimme a sec...")
        return

    if content[0].startswith("!"):
        keyword = content[0][1:]
        if keyword in command_list:
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

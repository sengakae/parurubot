import discord
import json
import random
import requests
import os

from dotenv import load_dotenv
from discord.ext import commands

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WEATHER_URL = os.getenv("WEATHER_URL")
WEATHER_TOKEN = os.getenv("WEATHER_TOKEN")

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
            quotes = data["quotes"]
    except:
        await ctx.send("No quotes found.")
        return
    
    await ctx.send(random.choice(quotes))

@bot.command(name="quote", help="prints the saved quote")
async def quote(ctx, number):
    try:
        with open("quotes.json", "r") as r:
            data = json.load(r)
            quotes = data["quotes"]
    except:
        await ctx.send("No quotes found.")
        return
    
    number = int(number)
    if 0 < number < len(quotes):
        await ctx.send(quotes[number - 1])
    else:
        await ctx.send("Invalid number.")

@bot.command(name="add", help="saves the following string as a quote")
async def add(ctx, quote):
    def add_quote(quote, file="quotes.json"):
        with open(file, "r+") as fw:
            j = json.load(fw)
            j["quotes"].append(quote)
            with open(file, "w+") as wp:
                wp.write(json.dumps(j))

    try:
        with open("quotes.json", "r"):
            pass
    except:
        with open("quotes.json", "w+") as wp:
            wp.write('{"quotes" : []}')
    finally:
        add_quote(quote)
        await ctx.send(f"Added: {quote}")

@bot.command(name="purge", help="purges the last [number] lines (max: 100)")
async def purge(ctx, number):
    lines = int(number)
    await ctx.channel.purge(limit=lines)

@bot.command(name="weather", help="prints weather information of city")
async def weather(ctx, *, city: str):
    url = f"{WEATHER_URL}appid={WEATHER_TOKEN}&q={city}"
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

                embed = discord.Embed(title=f"Weather in {city.capitalize()}, {country}",
                                    color=ctx.guild.me.top_role.color,
                                    timestamp=ctx.message.created_at,)
                embed.add_field(name="Description", value=f"**{description_main} - {description_info}**", inline=False)
                embed.add_field(name="Temperature", value=f"**{current_temp}°C**", inline=False)
                embed.add_field(name="Humidity", value=f"**{current_humidity}%**", inline=False)
                embed.set_footer(text=f"Requested by {ctx.author.display_name}")

                await ctx.send(embed=embed)
        else:
            await ctx.send("City not found.")
    except:
        ctx.send("An error occurred while fetching weather info.")

bot.run(DISCORD_TOKEN)
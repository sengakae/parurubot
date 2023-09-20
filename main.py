import discord
import json
import random
import requests
import os

from dotenv import load_dotenv
from discord.ext import commands

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WEATHER_TOKEN = os.getenv("WEATHER_TOKEN")

weather_url = "http://api.openweathermap.org/data/2.5/weather?"

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

command_list = ['rquote', 'purge', 'weather', 'add']

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
async def purge(ctx, number):
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

@bot.event
async def on_message(ctx):
    if ctx.author == bot.user:
        return
    
    content = ctx.content.split()
    if not content[0].startswith("!"):
        return

    keyword = content[0][1:]
    if keyword in command_list:
        await bot.process_commands(ctx)
        return
    else:
        with open("quotes.json", "r") as fw:
            quotes = json.load(fw)
            if keyword in quotes:
                await ctx.channel.send(quotes[keyword])

bot.run(DISCORD_TOKEN)